"""Game session use case — move routing, reconnection, and seat teardown.

Layer: application (server/application)
Owns: routing an in-game frame to the room that should answer it, rebinding a
returning client onto its seat, and deciding whether a dropped socket frees a
seat or opens a reconnect countdown.
Must not own: move legality (the engine decides), countdown timing
(DisconnectHandler), or socket transport.
"""

import logging
from typing import Any, Dict, Optional

from shared.model.game_state import Result

from server.application.dtos.frame_fields import FIELD_FROM, FIELD_TO
from server.domain.matchmaking.queue import MatchmakingQueue
from server.application.room_manager import RoomManager

_LOGGER = logging.getLogger(__name__)


class GameSessionUseCase:
    """Handles frames and lifecycle events for an already-seated player."""

    def __init__(self, room_manager: RoomManager, matchmaker: MatchmakingQueue) -> None:
        self._room_manager = room_manager
        self._matchmaker = matchmaker

    async def submit_move(self, session: Any, msg: Dict[str, Any]) -> Result[None, str]:
        """Route a move frame to the sender's room."""
        from_sq = msg.get(FIELD_FROM)
        to_sq = msg.get(FIELD_TO)
        if not from_sq or not to_sq:
            return Result.fail("Move message requires 'from' and 'to' fields")

        room = self._room_manager.find_room_by_session(session)
        if room is None:
            return Result.fail("You are not seated in a game")

        return await room.handle_move(session, from_sq, to_sq)

    async def reconnect(
        self, authenticated_username: str, claimed_username: Optional[str], websocket: Any
    ) -> Result[Any, str]:
        """Rebind a returning client onto its existing (disconnected) seat.

        The seat to rebind is always the authenticated identity from this
        connection's own auth handshake — *claimed_username*, if the reconnect
        frame carried one, is accepted only as a same-user sanity check, so a
        client can never reconnect onto someone else's seat by naming it.
        """
        if claimed_username and claimed_username != authenticated_username:
            return Result.fail("Cannot reconnect as a different user")

        # Located by identity across every room, since a reconnect arrives on a
        # fresh socket with no session object to index by.
        room = self._room_manager.find_room_by_username(authenticated_username)
        if room is None:
            return Result.fail(self._no_disconnected_seat(authenticated_username))

        session = await room.handle_reconnect(authenticated_username, websocket)
        if session is None:
            return Result.fail(self._no_disconnected_seat(authenticated_username))

        _LOGGER.info("Reconnected: %s", authenticated_username)
        return Result.ok(session)

    async def handle_connection_closed(self, session: Any) -> None:
        """Tear down or preserve a session's seat once its socket loop exits.

        Leaving the queue comes first and unconditionally: a player who drops
        while waiting must not stay pairable, or the matchmaker would seat a
        phantom opponent into a room nobody is listening to.

        A seated player mid-game gets a disconnect countdown instead of an
        immediate seat teardown — and keeps its room index entry, which is how
        the returning socket finds its way back.
        """
        await self._matchmaker.leave_queue(session)

        room = self._room_manager.find_room_by_session(session)
        if room is None or not room.handle_disconnect(session):
            session.disconnect()
            if room is not None:
                room.remove_participant(session)
            self._room_manager.release_session(session)
        _LOGGER.info("Disconnected: %s", session.username)

    @staticmethod
    def _no_disconnected_seat(username: str) -> str:
        return f"No disconnected session found for '{username}'"
