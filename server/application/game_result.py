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
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from shared.config import consts
from shared.io.moves_log import MoveLogEntry, parse_notation

if TYPE_CHECKING:
    from server.application.capture_log import CaptureRecord

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


def persisted_moves_from_log(
    entries: List[MoveLogEntry],
    captures: Optional[Sequence["CaptureRecord"]] = None,
) -> List[PersistedMove]:
    """Decompose MovesLog notation into per-column move rows, oldest first.

    Unparseable entries are skipped rather than raised on, matching MovesLog's
    own tolerance for hand-editable notation — one malformed row must not sink
    a whole game's history. move_number counts only the rows that survive, so
    the sequence stays gap-free.

    When *captures* is given, each capture is attributed to the capturing move
    by that move's own endpoints: a capture carries its captor's (from, to),
    and it annotates the earliest such move arriving at or after the capture
    instant. The arrival square/instant alone cannot serve as the key — a
    collision capture happens mid-transit, before the captor arrives and away
    from its destination, and en passant strikes the bypassed pawn's square.
    A capture whose capturing move never completes (an airborne strike, or a
    captor itself cut down mid-flight) simply annotates nothing, consistent
    with this function's skip-don't-raise stance.
    """
    pending_captures = _captures_by_captor_move(captures)
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
                captured_piece=_claim_captures(
                    pending_captures, from_square, to_square, int(entry.time_ms)
                ),
                timestamp=float(entry.time_ms),
            )
        )
    return rows


def _captures_by_captor_move(
    captures: Optional[Sequence["CaptureRecord"]],
) -> Dict[Tuple[str, str], List["CaptureRecord"]]:
    """Group captures, oldest first, under their captor movement's endpoints."""
    grouped: Dict[Tuple[str, str], List["CaptureRecord"]] = {}
    for capture in captures or ():
        grouped.setdefault((capture.captor_from, capture.captor_to), []).append(capture)
    return grouped


def _claim_captures(
    pending: Dict[Tuple[str, str], List["CaptureRecord"]],
    from_square: str,
    to_square: str,
    arrival_ms: int,
) -> Optional[str]:
    """Consume every capture the move arriving at *arrival_ms* is responsible for.

    Moves are processed oldest-first, so claiming only captures at or before
    this arrival attributes each one to the earliest completing candidate — a
    later identical (from, to) transit cannot steal an earlier move's capture,
    nor donate its own backwards. One transit can take several pieces (a
    mid-path collision and then an arrival capture); the single database
    column records them comma-joined.
    """
    waiting = pending.get((from_square, to_square))
    if not waiting:
        return None
    claimed = [capture for capture in waiting if capture.at_ms <= arrival_ms]
    if not claimed:
        return None
    pending[(from_square, to_square)] = [
        capture for capture in waiting if capture.at_ms > arrival_ms
    ]
    return ",".join(capture.piece_type for capture in claimed)


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
