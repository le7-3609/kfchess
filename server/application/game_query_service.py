"""Read-side query service — the twin of GamePersistenceService.

Layer: application (server/application)
Owns: turning the Database's raw read rows into plain, immutable DTOs
(GameReplay, ReplayMove, LeaderboardRow) so presentation never sees SQL tuples,
and the not-found signal (None) for an unknown game.
Must not own: SQL (Database owns queries), HTTP shapes (presentation formats
DTOs into JSON/PGN), or game rules. This is the thin read seam between the
database, which yields tuples, and the HTTP API, which wants named values.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from server.infrastructure.database.database import DEFAULT_LEADERBOARD_LIMIT, Database


@dataclass(frozen=True)
class ReplayMove:
    """One resolved move as a replay/PGN reader consumes it.

    *timestamp* is the game-clock instant (ms elapsed) the move landed, straight
    from MovesLog, so a replay can reproduce the game's real-time pacing.
    """

    move_number: int
    from_square: str
    to_square: str
    piece_type: str
    piece_color: str
    captured_piece: Optional[str]
    timestamp: float


@dataclass(frozen=True)
class GameReplay:
    """A completed game with usernames resolved and moves in play order."""

    game_id: int
    room_id: str
    white_player_id: int
    black_player_id: int
    white_username: str
    black_username: str
    winner_id: Optional[int]
    result: str
    white_elo_before: int
    white_elo_after: int
    black_elo_before: int
    black_elo_after: int
    started_at: str
    ended_at: str
    moves: List[ReplayMove]


@dataclass(frozen=True)
class LeaderboardRow:
    """One leaderboard entry: a player ranked by ELO."""

    username: str
    elo: int
    total_games: int
    wins: int


class GameQueryService:
    """Read completed games and rankings back out as DTOs.

    The write-side twin, GamePersistenceService, turns live game state into
    rows; this turns rows back into the plain values presentation formats.
    """

    def __init__(self, database: Database) -> None:
        self._database = database

    async def get_replay(self, game_id: int) -> Optional[GameReplay]:
        """Assemble a game and its ordered moves, or None if unknown."""
        game_row = await self._database.get_game(game_id)
        if game_row is None:
            return None
        move_rows = await self._database.get_moves(game_id)
        return self._replay_from_rows(game_row, move_rows)

    async def get_leaderboard(self, limit: int = DEFAULT_LEADERBOARD_LIMIT) -> List[LeaderboardRow]:
        """Top players by ELO as DTOs, highest first."""
        rows = await self._database.get_leaderboard(limit)
        return [
            LeaderboardRow(username=username, elo=elo, total_games=total_games, wins=wins)
            for username, elo, total_games, wins in rows
        ]

    @staticmethod
    def _replay_from_rows(game_row: Tuple, move_rows: List[Tuple]) -> GameReplay:
        (
            game_id, room_id, white_player_id, black_player_id,
            winner_id, result,
            white_elo_before, white_elo_after,
            black_elo_before, black_elo_after,
            started_at, ended_at,
            white_username, black_username,
        ) = game_row
        return GameReplay(
            game_id=game_id,
            room_id=room_id,
            white_player_id=white_player_id,
            black_player_id=black_player_id,
            white_username=white_username,
            black_username=black_username,
            winner_id=winner_id,
            result=result,
            white_elo_before=white_elo_before,
            white_elo_after=white_elo_after,
            black_elo_before=black_elo_before,
            black_elo_after=black_elo_after,
            started_at=started_at,
            ended_at=ended_at,
            moves=[
                ReplayMove(
                    move_number=move_number,
                    from_square=from_square,
                    to_square=to_square,
                    piece_type=piece_type,
                    piece_color=piece_color,
                    captured_piece=captured_piece,
                    timestamp=timestamp,
                )
                for (
                    move_number, from_square, to_square,
                    piece_type, piece_color, captured_piece, timestamp,
                ) in move_rows
            ],
        )
