"""Room use case — named-room creation, joining, and seat announcement.

Layer: application (server/application)
Owns: the orchestration of a client's lobby request into a seat — the
already-seated guard, withdrawal from the queue before seating, and telling
both sides when a room fills.
Must not own: room-id generation or seat invariants (RoomManager and the
domain GameRoom own those), nor socket transport.
"""

import logging
from typing import Any, Dict

from shared.model.game_state import Result

from server.application.dtos import ERROR_ALREADY_SEATED
from server.domain.matchmaking.queue import MatchmakingQueue
from server.domain.room.room_role import RoomRole
from server.application.game_room import GameRoom
from server.application.dtos.response_frames import build_game_start_message
from server.application.room_manager import RoomManager

_LOGGER = logging.getLogger(__name__)


class RoomUseCase:
    """Creates and fills named rooms on behalf of lobby clients."""

    def __init__(self, room_manager: RoomManager, matchmaker: MatchmakingQueue) -> None:
        self._room_manager = room_manager
        self._matchmaker = matchmaker

    async def create(self, session: Any) -> Result[str, str]:
        """Allocate a private named room, seating the creator as White."""
        if self._room_manager.find_room_by_session(session) is not None:
            return Result.fail(ERROR_ALREADY_SEATED)

        await self._matchmaker.leave_queue(session)
        room_id = self._room_manager.create_room(session)
        return Result.ok(room_id)

    async def join(self, session: Any, msg: Dict[str, Any]) -> Result[RoomRole, str]:
        """Seat a client into a named room as Black, or as a spectator if it is full.

        On success the seat has already been announced to everyone who needs to
        know; the returned role only tells the caller which acknowledgement, if
        any, still belongs to the requester.
        """
        room_id = msg.get("room_id")
        if not room_id or not isinstance(room_id, str):
            return Result.fail("join_room requires a 'room_id' field")

        if self._room_manager.find_room_by_session(session) is not None:
            return Result.fail(ERROR_ALREADY_SEATED)

        await self._matchmaker.leave_queue(session)
        try:
            role = self._room_manager.join_room(room_id, session)
        except KeyError as err:
            return Result.fail(str(err))

        room = self._room_manager.get_room(room_id)
        if role == RoomRole.VIEWER:
            await self._announce_viewer_seat(session, room)
        else:
            await self._announce_seat(session, room, role)
        return Result.ok(role)

    async def _announce_seat(self, session: Any, room: GameRoom, role: RoomRole) -> None:
        """Tell a newly-seated player (and any waiting opponent) that play begins.

        Seating Black is what completes the room, so that is the only point at
        which the tick runner starts and both sides learn each other's names.
        """
        opponent = room.opponent_of(session)
        if role != RoomRole.BLACK_PLAYER or opponent is None:
            await session.send(
                build_game_start_message(role.value, "Waiting for opponent...", room.room_id)
            )
            return

        await opponent.send(
            build_game_start_message(RoomRole.WHITE_PLAYER.value, session.username, room.room_id)
        )
        await session.send(
            build_game_start_message(RoomRole.BLACK_PLAYER.value, opponent.username, room.room_id)
        )
        await room.start()

    async def _announce_viewer_seat(self, session: Any, room: GameRoom) -> None:
        """Tell a spectator which room they've joined.

        A `game_start`-shaped frame — not just the informational note the
        presentation layer also sends — is what drives the client's existing
        `color == "viewer"` handling into read-only mode; without it the
        client has nothing telling it to open the game window at all.
        """
        await session.send(build_game_start_message(RoomRole.VIEWER.value, "", room.room_id))
