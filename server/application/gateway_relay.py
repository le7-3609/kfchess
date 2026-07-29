"""Gateway relay — carries broker traffic to and from the sockets this process holds.

Layer: application (server/application)
Owns: the subscriptions a gateway keeps on behalf of its connected clients, the
forwarding of a remote room's frames down a local socket, and the forwarding of
a local client's commands up to whichever instance owns their room.
Must not own: game rules, room ownership, or the broker itself.

This is the half of Step 7's gateway/authority split that faces the player. It
does no game logic at all: it turns "a frame arrived on a subject I subscribed
to" into "write it to this socket", and "this client sent a move for a room I do
not own" into "publish it to that room's command subject". Everything it
forwards is opaque to it.

**Two subscriptions per seated client, and why they are separate.**

* `session.<username>.frames` — the per-recipient frames. Opened when the
  session is established and held for its whole life, because it also carries
  the `game_start` that tells the client it *has* a room. Waiting until the room
  is known would mean missing the frame that announces it.
* `room.<room_id>.events` — the shared event stream. Opened when the client is
  seated and shared between every local client in that room, so two players on
  one gateway cost one subscription rather than two.

**Reference counting the room subscription** matters more than it looks. A
spectator leaving must not silently cancel the players' event stream, and the
last participant leaving must not leave a subscription delivering into nothing —
in a fleet at this size, a leaked subscription per finished room is a leak of
83,000 per second.
"""

import logging
from typing import Any, Dict, Optional, Set

from server.application.dtos import frame_fields as ff
from server.application.dtos import network_frames as nf
from server.domain.coordination.broker import (
    room_commands_subject,
    room_events_subject,
    session_frames_subject,
)

_LOGGER = logging.getLogger(__name__)


class GatewayRelay:
    """Bridges the broker and the WebSocket sessions this process holds."""

    def __init__(self, broker: Any) -> None:
        self._broker = broker
        self._session_subscriptions: Dict[str, Any] = {}
        self._room_subscriptions: Dict[str, Any] = {}
        # Which local sessions are in each room, so a room subscription is
        # dropped when the last one leaves and not before.
        self._room_members: Dict[str, Set[str]] = {}
        self._sessions: Dict[str, Any] = {}

    async def attach_session(self, session: Any) -> None:
        """Subscribe to this player's own frame subject and forward to its socket."""
        username = session.username
        if username in self._session_subscriptions:
            # A reconnect binds a new socket to the same identity; the
            # subscription is keyed by identity, so it is already correct.
            self._sessions[username] = session
            return

        self._sessions[username] = session
        self._session_subscriptions[username] = await self._broker.subscribe(
            session_frames_subject(username), self._forwarder(username)
        )
        _LOGGER.debug("Relay attached for %s", username)

    async def detach_session(self, session: Any) -> None:
        """Drop this player's subscriptions once their socket is truly gone."""
        username = session.username
        subscription = self._session_subscriptions.pop(username, None)
        if subscription is not None:
            await subscription.unsubscribe()
        self._sessions.pop(username, None)
        for room_id in list(self._room_members):
            await self.leave_room(room_id, username)
        _LOGGER.debug("Relay detached for %s", username)

    async def join_room(self, room_id: str, username: str) -> None:
        """Start forwarding *room_id*'s event stream to this player's socket.

        Idempotent per player, and shared across players: the second member of a
        room on this gateway joins the existing subscription.
        """
        members = self._room_members.setdefault(room_id, set())
        already_subscribed = bool(members)
        members.add(username)
        if already_subscribed:
            return

        self._room_subscriptions[room_id] = await self._broker.subscribe(
            room_events_subject(room_id), self._room_forwarder(room_id)
        )
        _LOGGER.debug("Relay subscribed to room %s", room_id)

    async def leave_room(self, room_id: str, username: str) -> None:
        """Stop forwarding once the last local member of *room_id* has gone."""
        members = self._room_members.get(room_id)
        if not members:
            return
        members.discard(username)
        if members:
            return

        del self._room_members[room_id]
        subscription = self._room_subscriptions.pop(room_id, None)
        if subscription is not None:
            await subscription.unsubscribe()
        _LOGGER.debug("Relay unsubscribed from room %s", room_id)

    async def forward_command(self, room_id: str, username: str, frame: Dict[str, Any]) -> None:
        """Send a local client's command to whichever instance owns their room.

        The username is stamped on by the gateway rather than read from the
        frame. The authority on the other end has no socket to authenticate
        against, so it has to trust the sender for identity — which means the
        sender must be the one that establishes it, from its own verified
        handshake, and never the client.
        """
        await self._broker.publish(
            room_commands_subject(room_id), {**frame, "username": username}
        )

    def _forwarder(self, username: str):
        async def forward(payload: Dict[str, Any]) -> None:
            await self._write(username, payload)

        return forward

    def _room_forwarder(self, room_id: str):
        async def forward(payload: Dict[str, Any]) -> None:
            for username in list(self._room_members.get(room_id, ())):
                await self._write(username, payload)

        return forward

    async def _write(self, username: str, payload: Dict[str, Any]) -> None:
        """Write one forwarded frame to a local socket, if it is still there.

        A frame for a session that has already gone is dropped rather than
        logged as an error: the broker has no way to know a socket closed a
        millisecond ago, so this is the normal end of every game, not a fault.
        """
        session = self._sessions.get(username)
        if session is None:
            return
        await self._follow_room_membership(username, payload)
        try:
            await session.send(payload)
        except Exception as exc:
            _LOGGER.warning("Forwarding a frame to %s failed: %s", username, exc)

    async def _follow_room_membership(self, username: str, payload: Dict[str, Any]) -> None:
        """Subscribe to a room this player was just seated in, or leave one they left.

        The one place this relay reads a frame rather than forwarding it opaquely,
        and it earns the exception: `game_start` is precisely the frame that says
        "you are now in this room", and the gateway holding the socket is who has
        to act on it.

        Without this, a player seated by a *remote* instance — matched across
        replicas, so their seat was created by a `RoomManager` in another process
        that never saw their socket — would receive their own per-recipient
        frames but never the room's shared event stream. Their opponent's moves
        would simply never arrive, and the first thing to repair it would be the
        reconciliation snapshot seconds later.
        """
        frame_type = payload.get(ff.FIELD_TYPE)
        room_id = payload.get(ff.FIELD_ROOM_ID)

        if frame_type == nf.MSG_GAME_START and room_id:
            await self.join_room(room_id, username)
        elif frame_type == nf.MSG_GAME_END:
            # The room is over, so its subscription is pure cost from here on.
            # The frame carries no room id, so every membership this player holds
            # is released — which is correct, since a player is only ever in one.
            for held in [r for r, members in self._room_members.items() if username in members]:
                await self.leave_room(held, username)

    async def close(self) -> None:
        """Drop every subscription this relay holds, on server shutdown."""
        for subscription in list(self._session_subscriptions.values()):
            await subscription.unsubscribe()
        for subscription in list(self._room_subscriptions.values()):
            await subscription.unsubscribe()
        self._session_subscriptions.clear()
        self._room_subscriptions.clear()
        self._room_members.clear()
        self._sessions.clear()
