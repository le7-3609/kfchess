"""Room manager — handles room creation, named lookup, joining, and viewer assignment.

Owns: room collection registry, unique room ID generation (6-char alphanumeric),
the participant -> room index, and room lifecycle routing.
Must not own: game rules, chess board engine logic, or network transport.

Rooms are constructed here rather than by callers so every room — matchmade,
named, or bot — is built with the same collaborators (database for ELO
persistence, event loop for broadcast scheduling) and the same expiry hook.

That hook is what keeps the registry bounded: a room announces its own end
rather than being polled for it, and this manager reaps it on being told.
"""

import asyncio
import logging
import random
import string
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from server.infrastructure.database.database import Database
from server.application.game_room import GameRoom
from server.domain.room.room_role import RoomRole

_LOGGER = logging.getLogger(__name__)

ROOM_ID_LENGTH = 6
ROOM_ID_CHARS = string.ascii_uppercase + string.digits
MAX_ROOM_ID_ATTEMPTS = 100


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
    ) -> None:
        self._database = database
        self._loop = loop
        self._rooms: Dict[str, GameRoom] = {}
        self._session_rooms: Dict[Any, str] = {}

    @property
    def room_count(self) -> int:
        return len(self._rooms)

    def all_rooms(self) -> List[GameRoom]:
        return list(self._rooms.values())

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
            on_room_expired=self._reap_room,
        )
        room.add_player(creator_session)
        self._rooms[room_id] = room
        self._session_rooms[creator_session] = room_id
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
        """Find the room holding a disconnected seat for *username*.

        Used by the reconnect handshake, which arrives on a fresh socket with no
        session object yet — so the seat can only be located by identity.
        """
        for room in self._rooms.values():
            seat = room.find_player_by_username(username)
            if seat is not None and room.disconnect_handler.is_disconnected(seat):
                return room
        return None

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

        del self._rooms[normalized_id]
        self._session_rooms = {
            session: r_id for session, r_id in self._session_rooms.items() if r_id != normalized_id
        }
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
