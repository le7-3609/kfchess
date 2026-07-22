"""Completed-game DTOs — the immutable snapshot handed to persistence.

Layer: application (server/application)
Owns: the plain-value description of a finished game (players, result, ELO
deltas, timing) and its parsed moves, carried from GameRoom to the persistence
layer.
Must not own: live session/board objects, SQL, or the rules deciding which
games persist — this is data only. GameRoom builds it from session and event
state; GamePersistenceService reads it. Keeping it free of live objects is what
lets the database layer depend on data alone, never on network sessions.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from shared.config import consts
from shared.io.moves_log import MoveLogEntry, parse_notation

# MovesLog records piece color as a single-letter code; the moves table stores
# the readable word, so a replay reader never has to know the engine's alphabet.
_COLOR_WORD = {consts.COLOR_WHITE: "white", consts.COLOR_BLACK: "black"}


@dataclass(frozen=True)
class PersistedMove:
    """One resolved move, already decomposed into database columns."""

    move_number: int
    from_square: str
    to_square: str
    piece_type: str
    piece_color: str
    captured_piece: Optional[str]
    timestamp: float


def persisted_moves_from_log(entries: List[MoveLogEntry]) -> List[PersistedMove]:
    """Decompose MovesLog notation into per-column move rows, oldest first.

    Unparseable entries are skipped rather than raised on, matching MovesLog's
    own tolerance for hand-editable notation — one malformed row must not sink
    a whole game's history. move_number counts only the rows that survive, so
    the sequence stays gap-free.
    """
    rows: List[PersistedMove] = []
    for entry in entries:
        parsed = parse_notation(entry.notation)
        if parsed is None:
            continue
        from_square, to_square = entry.notation[1:].split(consts.NOTATION_MOVE_SEPARATOR)
        rows.append(
            PersistedMove(
                move_number=len(rows) + 1,
                from_square=from_square,
                to_square=to_square,
                piece_type=parsed.piece_type,
                piece_color=_COLOR_WORD.get(entry.color, entry.color),
                captured_piece=None,
                timestamp=float(entry.time_ms),
            )
        )
    return rows


@dataclass(frozen=True)
class GameResult:
    """Immutable game-end snapshot, built once at game end.

    *winner_id* is None for a draw; otherwise the winning player's user id.
    *result* is the terminal reason (e.g. "checkmate", "stalemate", "timeout").
    ELO before/after are captured per seat so a replay can show the swing.
    """

    room_id: str
    white_player_id: int
    black_player_id: int
    winner_id: Optional[int]
    result: str
    white_elo_before: int
    white_elo_after: int
    black_elo_before: int
    black_elo_after: int
    started_at: datetime
    ended_at: datetime
    moves: List[PersistedMove]
