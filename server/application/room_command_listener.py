"""Room command listener — the authority's inbound half of the broker split.

Layer: application (server/application)
Owns: subscribing to a room's command subject on the instance that owns it, and
turning an arriving command frame into the same use-case call a locally-held
socket would have produced.
Must not own: move legality (the engine decides), transport (the broker), or
which instance owns a room (the lease decides, and this only listens for the
rooms it was told to).

**Only the owner subscribes.** That is the structural half of "one room, one
authority": a command reaches exactly one process because exactly one process is
listening for it, rather than because every process agreed not to act. An
instance that loses a room's lease unsubscribes, and from that moment it cannot
act on the room even if it still believes it owns one.

**Identity comes from the sender, not the frame.** There is no socket here to
authenticate against, so the gateway stamps the username it verified during its
own handshake and this trusts that. The client never supplies it — which is the
same rule `GameSessionUseCase.reconnect` already applies to the seat a reconnect
rebinds.
"""

import logging
from typing import Any, Dict

from server.application.dtos.frame_fields import FIELD_FROM, FIELD_MOVE_ID, FIELD_TO, FIELD_TYPE
from server.application.dtos.network_frames import MSG_MOVE
from server.application.dtos.response_frames import build_error_message
from server.domain.coordination.broker import room_commands_subject

_LOGGER = logging.getLogger(__name__)

FIELD_USERNAME = "username"


class RoomCommandListener:
    """Receives commands for the rooms this instance owns."""

    def __init__(self, broker: Any, room_manager: Any) -> None:
        self._broker = broker
        self._room_manager = room_manager
        self._subscriptions: Dict[str, Any] = {}

    async def listen(self, room_id: str) -> None:
        """Begin accepting commands for a room this instance owns."""
        if room_id in self._subscriptions:
            return
        self._subscriptions[room_id] = await self._broker.subscribe(
            room_commands_subject(room_id), self._handler(room_id)
        )
        _LOGGER.debug("Listening for commands on room %s", room_id)

    async def stop_listening(self, room_id: str) -> None:
        """Stop accepting commands — on reap, or on losing the room's lease."""
        subscription = self._subscriptions.pop(room_id, None)
        if subscription is not None:
            await subscription.unsubscribe()
            _LOGGER.debug("Stopped listening for commands on room %s", room_id)

    async def close(self) -> None:
        for room_id in list(self._subscriptions):
            await self.stop_listening(room_id)

    def _handler(self, room_id: str):
        async def handle(frame: Dict[str, Any]) -> None:
            await self._dispatch(room_id, frame)

        return handle

    async def _dispatch(self, room_id: str, frame: Dict[str, Any]) -> None:
        """Route one arriving command to the room that should answer it.

        A command for a room this instance no longer holds is dropped rather
        than acted on: between the publish and the delivery the room may have
        been reaped or handed to another owner, and acting on it would be a
        second authority writing to the same game.
        """
        room = self._room_manager.get_room(room_id)
        if room is None:
            _LOGGER.debug("Command for room %s arrived after it was released", room_id)
            return

        username = frame.get(FIELD_USERNAME)
        seat = room.find_player_by_username(username) if username else None
        if seat is None:
            _LOGGER.warning("Command for room %s named no seated player (%r)", room_id, username)
            return

        if frame.get(FIELD_TYPE) == MSG_MOVE:
            await self._apply_move(room, seat, frame)
            return
        _LOGGER.warning("Unhandled remote command type %r on room %s", frame.get(FIELD_TYPE), room_id)

    @staticmethod
    async def _apply_move(room: Any, seat: Any, frame: Dict[str, Any]) -> None:
        """Execute a remote player's move, answering a refusal to them alone.

        The error goes back through the seat rather than onto the room's event
        subject, because a rejected move is that player's business — publishing
        it to the room would tell their opponent what they tried.
        """
        from_square, to_square = frame.get(FIELD_FROM), frame.get(FIELD_TO)
        if not from_square or not to_square:
            _LOGGER.warning("Remote move frame lacked 'from' or 'to'")
            return

        move_id = frame.get(FIELD_MOVE_ID)
        if move_id is not None and not isinstance(move_id, str):
            move_id = None

        result = await room.handle_move(seat, from_square, to_square, move_id=move_id)
        if not result.is_ok:
            await seat.send(build_error_message(result.error))
