"""Game persistence service — saves finished games to the database.

Layer: application (server/application)
Owns: orchestrating the atomic save of a completed game (game row + moves +
refreshed statistics) through the Database, and the gate that keeps active or
unrateable games out of history.
Must not own: SQL or transaction mechanics (Database owns the single
transaction), game rules, or ELO math (GameRoom computes the outcome). This is
the thin seam between GameRoom, which holds live session/timing state, and the
database, which wants only plain values.
"""

import logging
from typing import Optional

from server.application.game_result import GameResult
from server.infrastructure.database.database import Database

_LOGGER = logging.getLogger(__name__)


class GamePersistenceService:
    """Persist completed games to the database atomically.

    Called from GameRoom's game-end and forfeit paths after ELO deltas are
    computed. Bot games never reach here because GameRoom only builds a
    GameResult once it has a rateable, two-human outcome.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def persist_game(self, game_result: GameResult) -> Optional[int]:
        """Save the game, its moves, and both players' stats in one transaction.

        Delegates the all-or-nothing write to the Database. Returns the new
        game's id, or None if the save was rolled back (e.g. a duplicate
        room_id from a game-end and forfeit racing the same room).
        """
        game_id = await self._database.save_completed_game(game_result, game_result.moves)
        if game_id is None:
            _LOGGER.warning("Game for room %s was not persisted", game_result.room_id)
        return game_id
