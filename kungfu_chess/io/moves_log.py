"""Move history recording (Layer 6/IO) — an EventBus subscriber.

Owns: an in-memory, ordered record of every resolved move, as algebraic-ish
notation strings, for display and for GameHistoryStore to persist.
Must not own: game rules, board mutation, or persistence (that's
game_history_store.py's job).
"""

import re
from dataclasses import dataclass
from typing import List, Optional

from kungfu_chess.events import Event, Observer, PieceMovedEvent
from kungfu_chess.model.position import Position

_FILES = "abcdefgh"
_RANKS = 8

_NOTATION_PATTERN = re.compile(r"^([KQRBNP])([a-h][1-8])-([a-h][1-8])$")


def _algebraic(pos: Position) -> str:
    """Board Position -> algebraic square name (e.g. row=6,col=4 -> 'e2')."""
    file_letter = _FILES[pos.col] if 0 <= pos.col < len(_FILES) else str(pos.col)
    rank_number = _RANKS - pos.row
    return f"{file_letter}{rank_number}"


def _position(square: str) -> Position:
    """Algebraic square name -> board Position. Inverse of _algebraic."""
    return Position(_RANKS - int(square[1]), _FILES.index(square[0]))


@dataclass(frozen=True)
class MoveLogEntry:
    color: str
    notation: str
    time_ms: int


@dataclass(frozen=True)
class ParsedNotation:
    """A notation string read back into board coordinates.

    *piece_type* is the type the piece had **on arrival**, which is not
    necessarily the type it left with: a promoting pawn is swapped for its
    promoted piece before the move event is published, so 'e7-e8' is logged as
    'Q', not 'P'. Readers must resolve the mover from *frm*, not from here.
    """

    piece_type: str
    frm: Position
    to: Position


def parse_notation(notation: str) -> Optional[ParsedNotation]:
    """Read a notation string written by MovesLog back into coordinates.

    Returns None for anything that does not match the format, so callers
    reading hand-editable save files degrade rather than raise.
    """
    match = _NOTATION_PATTERN.match(notation.strip())
    if match is None:
        return None
    return ParsedNotation(
        piece_type=match.group(1),
        frm=_position(match.group(2)),
        to=_position(match.group(3)),
    )


class MovesLog(Observer):
    """Records every resolved move announced on the EventBus.

    Each entry is stamped with the event's own arrival instant rather than
    whatever the clock reads when the log is asked, so a move keeps the time it
    actually landed even though several can resolve within one tick.
    """

    def __init__(self) -> None:
        self._entries: List[MoveLogEntry] = []

    def on_event(self, event: Event) -> None:
        if not isinstance(event, PieceMovedEvent):
            return
        notation = f"{event.piece_type}{_algebraic(event.frm)}-{_algebraic(event.to)}"
        self._entries.append(
            MoveLogEntry(color=event.color, notation=notation, time_ms=event.at_ms)
        )

    def entries(self) -> List[MoveLogEntry]:
        return list(self._entries)

    def entries_for(self, color: str) -> List[MoveLogEntry]:
        return [e for e in self._entries if e.color == color]
