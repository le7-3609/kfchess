"""Room manager — handles room creation, named lookup, joining, and viewer assignment.

Owns: room collection registry, unique room ID generation (6-char alphanumeric),
the participant -> room index, and room lifecycle routing.
Must not own: game rules, chess board engine logic, or network transport.

Rooms are constructed here rather than by callers so every room — matchmade,
named, or bot — is built with the same collaborators (database for ELO
persistence, event loop for broadcast scheduling) and the same expiry hook.

That hook is what keeps the registry bounded: a room announces its own end
rather than being polled for it, and this manager reaps it on being told.

**The directory.** The local registry above answers only for rooms this process
created, which stops being the whole truth the moment a second replica exists.
Every create, join and reap is therefore also published to a `SeatDirectory` /
`RoomDirectory`, so any replica can answer "where is this player seated" without
scanning anything. Publishing is deliberately detached and best-effort: the room
is fully playable locally before the directory hears about it, and a Redis round
trip must never sit in the middle of seating a player. `drain_directory_writes`
exists so a caller that needs the published state — a test, or a drain — can
wait for it explicitly instead of guessing.
"""

import asyncio
import logging
import random
import string
from dataclasses import dataclass
from typing import Any, Awaitable, Dict, List, Optional

from server.infrastructure.database.database import Database
from server.application.game_persistence_service import GamePersistenceService
from server.application.game_room import DEFAULT_RECONCILIATION_INTERVAL_SECONDS, GameRoom
from server.domain.coordination.directory import RoomDirectory, SeatDirectory, SeatLocation
from server.domain.room.room_role import RoomRole

_LOGGER = logging.getLogger(__name__)

ROOM_ID_LENGTH = 6
ROOM_ID_CHARS = string.ascii_uppercase + string.digits
MAX_ROOM_ID_ATTEMPTS = 100

# Identifies this process in every directory entry it writes. Empty in a single
# process, where "which replica" has only one possible answer.
DEFAULT_INSTANCE_ID = ""


@dataclass
class RoomInfo:
    room_id: str
    white_username: Optional[str]
    black_username: Optional[str]
    viewer_count: int
    state: str


class RoomManager:
    """Registry and manager for active game rooms."""

    def __init__(
        self,
        database: Optional[Database] = None,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        persistence_service: Optional[GamePersistenceService] = None,
        reconciliation_interval_seconds: float = DEFAULT_RECONCILIATION_INTERVAL_SECONDS,
        seat_directory: Optional[SeatDirectory] = None,
        room_directory: Optional[RoomDirectory] = None,
        instance_id: str = DEFAULT_INSTANCE_ID,
        broker: Optional[Any] = None,
        relay: Optional[Any] = None,
        command_listener: Optional[Any] = None,
    ) -> None:
        self._database = database
        self._loop = loop
        self._seat_directory = seat_directory
        self._room_directory = room_directory
        self._instance_id = instance_id
        # Handed to every room this manager builds, so a room's events reach
        # recipients whose sockets another replica holds.
        self._broker = broker
        # Told when a locally-held socket enters or leaves a room, so it can
        # subscribe that socket to the room's event stream. Notified from here
        # rather than from the use cases because this is the one place that
        # knows every way a session becomes a participant — matchmade, named,
        # spectating — and a notification wired per call site would miss one.
        self._relay = relay
        # Started for each room this instance creates and stopped when it is
        # reaped, so the subscription's lifetime *is* the ownership window —
        # there is no second flag saying "I own this" that could disagree.
        self._command_listener = command_listener
        # Detached directory writes, held so the loop cannot collect one
        # mid-flight — it keeps only weak references to tasks.
        self._directory_writes: set = set()
        # How often each room re-sends the whole board. Held here rather than in
        # the room because it is a fleet-wide tuning decision — the trade between
        # steady-state bandwidth and how long a client can stay drifted — not
        # something one room decides for itself.
        self._reconciliation_interval_seconds = reconciliation_interval_seconds
        # Built once and shared by every room so each finished game is saved
        # through the same connection; defaults from the database when omitted
        # so callers that only pass a database still get history persistence.
        self._persistence_service = persistence_service or (
            GamePersistenceService(database) if database is not None else None
        )
        self._rooms: Dict[str, GameRoom] = {}
        self._session_rooms: Dict[Any, str] = {}

    def attach_command_listener(self, listener: Any) -> None:
        """Supply the command listener after construction.

        A listener routes *into* this manager, so it cannot be built before it —
        the cycle is resolved by wiring it in afterwards rather than by having
        either one construct the other.
        """
        self._command_listener = listener

    @property
    def room_count(self) -> int:
        return len(self._rooms)

    @property
    def instance_id(self) -> str:
        """This process's name in the directory, for comparing against a seat's."""
        return self._instance_id

    def all_rooms(self) -> List[GameRoom]:
        return list(self._rooms.values())

    def active_countdown_count(self) -> int:
        """How many disconnect countdowns are running across every room.

        Exported as a metric because it is the only visible sign of a fleet-wide
        network problem that has not yet turned into forfeits: a spike here
        precedes a spike in games lost to disconnection by exactly the countdown
        duration.
        """
        return sum(room.disconnect_handler.countdown_count for room in self._rooms.values())

    def count_rooms_hosted_by(self, username: str) -> int:
        """How many live rooms *username* currently occupies a seat in.

        Derived from the rooms themselves rather than from a separate tally, so
        there is no second count that can drift when a room is reaped.
        """
        return sum(
            1
            for room in self._rooms.values()
            if room.find_player_by_username(username) is not None
        )

    def create_room(self, creator_session: Any) -> str:
        """Create a new game room with a unique 6-character alphanumeric ID.

        The creator takes the White seat; the room stays WAITING until a second
        participant fills Black.
        """
        room_id = self._generate_unique_room_id()
        room = GameRoom(
            room_id=room_id,
            loop=self._loop,
            database=self._database,
            persistence_service=self._persistence_service,
            on_room_expired=self._reap_room,
            reconciliation_interval_seconds=self._reconciliation_interval_seconds,
            broker=self._broker,
        )
        room.add_player(creator_session)
        self._rooms[room_id] = room
        self._session_rooms[creator_session] = room_id
        self._publish_room(room_id)
        self._publish_seat(creator_session, room_id)
        _LOGGER.info("Room %s created by %s", room_id, creator_session.username)
        return room_id

    def join_room(self, room_id: str, joiner_session: Any) -> RoomRole:
        """Join an existing room by room_id.

        First 2 joiners become White and Black players; subsequent joiners become Viewers.

        Raises:
            KeyError: If no room with *room_id* exists.
        """
        normalized_id = self._normalize(room_id)
        room = self._rooms.get(normalized_id)
        if room is None:
            raise KeyError(f"Room '{normalized_id}' does not exist")

        if not room.is_full:
            role = room.add_player(joiner_session)
            _LOGGER.info(
                "User %s joined room %s as player (%s)",
                joiner_session.username, normalized_id, role.value,
            )
        else:
            room.add_viewer(joiner_session)
            role = RoomRole.VIEWER
            _LOGGER.info("User %s joined room %s as spectator", joiner_session.username, normalized_id)

        self._session_rooms[joiner_session] = normalized_id
        self._publish_seat(joiner_session, normalized_id)
        return role

    def get_room(self, room_id: str) -> Optional[GameRoom]:
        return self._rooms.get(self._normalize(room_id))

    def find_room_by_session(self, session: Any) -> Optional[GameRoom]:
        """Resolve the room *session* currently participates in, if any."""
        room_id = self._session_rooms.get(session)
        if room_id is None:
            return None
        return self._rooms.get(room_id)

    def find_room_by_username(self, username: str) -> Optional[GameRoom]:
        """Find the *local* room holding a disconnected seat for *username*.

        Used by the reconnect handshake, which arrives on a fresh socket with no
        session object yet — so the seat can only be located by identity.

        Answers only for rooms this process runs. `locate_seat` is what answers
        for the fleet; this stays the fast path, because a client almost always
        reconnects to the replica the load balancer sends it to next, and one in
        N of those is the right one.
        """
        for room in self._rooms.values():
            seat = room.find_player_by_username(username)
            if seat is not None and room.disconnect_handler.is_disconnected(seat):
                return room
        return None

    async def locate_seat(self, username: str) -> Optional[SeatLocation]:
        """Where in the fleet *username* is seated, local or not.

        This is what replaces the scan: with a directory configured, a returning
        client that lands on any replica is answered from one keyed lookup
        rather than from whichever subset of rooms happens to be local.
        """
        if self._seat_directory is None:
            room = self.find_room_by_username(username)
            if room is None:
                return None
            return SeatLocation(username=username, room_id=room.room_id, replica=self._instance_id)
        return await self._seat_directory.find_seat(username)

    def _publish_seat(self, session: Any, room_id: str) -> None:
        self._relay_join(session, room_id)
        if self._seat_directory is None:
            return
        location = SeatLocation(
            username=session.username, room_id=room_id, replica=self._instance_id
        )
        self._schedule_directory_write(self._seat_directory.bind_seat(location))

    def _relay_join(self, session: Any, room_id: str) -> None:
        """Subscribe a locally-held socket to this room's event stream.

        Only for sessions with a real socket. A `RemoteSeat` is a stand-in for a
        player some other gateway holds; subscribing it here would forward that
        player's frames into a process that has nothing to write them to, and
        the gateway that *does* hold them has already subscribed itself.
        """
        if self._relay is None or getattr(session, "websocket", None) is None:
            return
        self._schedule_directory_write(self._relay.join_room(room_id, session.username))

    def _relay_leave(self, room_id: str, usernames: List[str]) -> None:
        if self._relay is None:
            return
        for username in usernames:
            self._schedule_directory_write(self._relay.leave_room(room_id, username))

    def _publish_room(self, room_id: str) -> None:
        if self._command_listener is not None:
            self._schedule_directory_write(self._command_listener.listen(room_id))
        if self._room_directory is None:
            return
        self._schedule_directory_write(
            self._room_directory.register_room(room_id, self._instance_id)
        )

    def _retract_room(self, room_id: str, seat_usernames: List[str]) -> None:
        """Withdraw a finished room and its seats from the directory.

        Without this a reaped room's entries survive until their TTL, and a
        player who starts a new game inside that window would be routed to the
        room they just left.
        """
        self._relay_leave(room_id, seat_usernames)
        if self._command_listener is not None:
            self._schedule_directory_write(self._command_listener.stop_listening(room_id))
        if self._room_directory is not None:
            self._schedule_directory_write(self._room_directory.release_room(room_id))
        if self._seat_directory is not None:
            for username in seat_usernames:
                self._schedule_directory_write(self._seat_directory.release_seat(username))

    def _schedule_directory_write(self, coroutine: Awaitable[None]) -> None:
        """Run a directory write detached, or discard it if there is no loop.

        No loop means no fleet either — an ad-hoc manager in a unit test — so
        closing the coroutine is the correct no-op rather than an error.
        """
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                coroutine.close()
                return
        task = loop.create_task(coroutine)
        self._directory_writes.add(task)
        task.add_done_callback(self._directory_writes.discard)

    async def drain_directory_writes(self) -> None:
        """Wait for every scheduled directory write to finish.

        Publishing is detached because it must not delay seating a player, which
        leaves callers who genuinely need the published state — a drain, or a
        test asserting on it — with nothing to await. This is that seam.
        """
        while self._directory_writes:
            await asyncio.gather(*list(self._directory_writes), return_exceptions=True)

    def release_session(self, session: Any) -> None:
        """Drop *session* from the participant index once its seat is truly gone.

        Deliberately separate from disconnection: a player riding out a
        reconnect countdown keeps its index entry, since that entry is how the
        returning socket finds its way back to the room.
        """
        self._session_rooms.pop(session, None)

    def remove_room(self, room_id: str) -> bool:
        normalized_id = self._normalize(room_id)
        if normalized_id not in self._rooms:
            return False

        departing = [
            session.username
            for session, r_id in self._session_rooms.items()
            if r_id == normalized_id
        ]
        del self._rooms[normalized_id]
        self._session_rooms = {
            session: r_id for session, r_id in self._session_rooms.items() if r_id != normalized_id
        }
        self._retract_room(normalized_id, departing)
        _LOGGER.info("Room %s removed", normalized_id)
        return True

    async def _reap_room(self, room_id: str) -> None:
        """Unregister a finished room and tear down the tasks it left running.

        Injected into every room this manager builds, and called back once that
        room's game reaches a terminal state. Without it a finished room stays
        indexed forever with its tick loop still running.

        Unregistering happens before the first await, so a late `join_room` or
        move can never be routed into a room that is already draining its tick
        loop. The room object outlives that only for as long as its own
        teardown needs it.
        """
        normalized_id = self._normalize(room_id)
        room = self._rooms.get(normalized_id)
        if room is None:
            return

        self.remove_room(normalized_id)
        await room.stop()
        _LOGGER.info("Room %s reaped: background tasks cancelled, resources reclaimed", normalized_id)

    def list_rooms(self) -> List[RoomInfo]:
        """List active room summaries for lobby view."""
        return [
            RoomInfo(
                room_id=r_id,
                white_username=room.white_player.username if room.white_player else None,
                black_username=room.black_player.username if room.black_player else None,
                viewer_count=room.viewer_count,
                state=room.state.value,
            )
            for r_id, room in self._rooms.items()
        ]

    def _generate_unique_room_id(self) -> str:
        for _ in range(MAX_ROOM_ID_ATTEMPTS):
            r_id = "".join(random.choices(ROOM_ID_CHARS, k=ROOM_ID_LENGTH))
            if r_id not in self._rooms:
                return r_id
        raise RuntimeError(f"Failed to generate unique room ID after {MAX_ROOM_ID_ATTEMPTS} attempts")

    @staticmethod
    def _normalize(room_id: str) -> str:
        """Room codes are shown uppercase, so accept any casing the client echoes back."""
        return room_id.upper().strip()
