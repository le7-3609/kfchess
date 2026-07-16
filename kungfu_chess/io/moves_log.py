"""Move history recording (Layer 6/IO) — the "moves log" counterpart to the
python_port GameSession listener, adapted to kfchess's MoveEventPublisher.

Owns: an in-memory, ordered record of every resolved move, as algebraic-ish
notation strings, for display and for GameHistoryStore to persist.
Must not own: game rules, board mutation, or persistence (that's
game_history_store.py's job).
"""

from dataclasses import dataclass
from typing import Callable, List, Optional

from kungfu_chess.engine.engine_interfaces import MoveEventListenerInterface
from kungfu_chess.model.position import Position

_FILES = "abcdefgh"


def _algebraic(pos: Position) -> str:
    """Board Position -> algebraic square name (e.g. row=6,col=4 -> 'e2')."""
    file_letter = _FILES[pos.col] if 0 <= pos.col < len(_FILES) else str(pos.col)
    rank_number = 8 - pos.row
    return f"{file_letter}{rank_number}"


@dataclass(frozen=True)
class MoveLogEntry:
    color: str
    notation: str
    time_ms: int


class MovesLog(MoveEventListenerInterface):
    """Subscribes to MoveEventPublisher and records every resolved move.

    *clock_ms* lets the caller supply the current game clock (e.g.
    ``lambda: state_repo.get_state().clock_ms``) since MoveEventPublisher
    itself does not pass a timestamp.
    """

    def __init__(self, clock_ms: Optional[Callable[[], int]] = None) -> None:
        self._entries: List[MoveLogEntry] = []
        self._clock_ms = clock_ms or (lambda: 0)

    def on_move(self, piece, frm: Position, to: Position) -> None:
        notation = f"{piece.piece_type}{_algebraic(frm)}-{_algebraic(to)}"
        self._entries.append(MoveLogEntry(color=piece.color, notation=notation, time_ms=self._clock_ms()))

    def entries(self) -> List[MoveLogEntry]:
        return list(self._entries)

    def entries_for(self, color: str) -> List[MoveLogEntry]:
        return [e for e in self._entries if e.color == color]
