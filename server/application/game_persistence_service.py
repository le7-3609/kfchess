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
from server.infrastructure.observability.server_metrics import ServerMetrics, server_metrics

_LOGGER = logging.getLogger(__name__)


class GamePersistenceService:
    """Persist completed games to the database atomically.

    Called from GameRoom's game-end and forfeit paths after ELO deltas are
    computed. Bot games never reach here because GameRoom only builds a
    GameResult once it has a rateable, two-human outcome.
    """

    def __init__(self, database: Database, metrics: Optional[ServerMetrics] = None) -> None:
        self._database = database
        self._metrics = metrics or server_metrics()

    async def persist_game(self, game_result: GameResult) -> Optional[int]:
        """Save the game, its moves, and both players' stats in one transaction.

        Delegates the all-or-nothing write to the Database. Returns the game's
        id — whether this call wrote it or found it already written — or None if
        the save actually failed.

        Finding the row already there is a success, not a warning. A natural
        game end and a disconnect forfeit can race the same room, and once
        persistence moves onto an at-least-once stream a redelivered event will
        reach a worker that already wrote the row. Logging that as "was not
        persisted", as this used to, describes a healthy system as a broken one.
        """
        outcome = await self._database.save_completed_game(game_result, game_result.moves)
        if outcome is None:
            _LOGGER.warning("Game for room %s was not persisted", game_result.room_id)
            return None

        if outcome.already_existed:
            self._metrics.games_already_persisted.increment()
            _LOGGER.info(
                "Room %s was already persisted as game %d", game_result.room_id, outcome.game_id
            )
        else:
            self._metrics.games_persisted.increment()
        return outcome.game_id
