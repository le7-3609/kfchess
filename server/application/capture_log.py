"""Capture recording — an EventBus subscriber, twin of shared.io.MovesLog.

Layer: application (server/application)
Owns: an in-memory, ordered record of every capture announced on the bus, keyed
so a resolved move can later be annotated with the piece it took. Lives here
rather than in shared/ because captured_piece is a persistence concern the game
engine has no reason to carry.
Must not own: game rules, board mutation, or drawing. Like MovesLog it only
records mid-tick — "events notify, they never draw" — so a slow subscriber can
never stall a half-resolved tick.
"""

from dataclasses import dataclass
from typing import List

from shared.config import consts
from shared.events import Event, Observer, PieceCapturedEvent
from shared.model.position import Position


def _algebraic(pos: Position) -> str:
    """Board Position -> algebraic square name, identical to MovesLog's mapping.

    Replicated (rather than imported from MovesLog's private helper) so a
    capture square matches the *to* square of the move that took it exactly.
    """
    files = consts.NOTATION_FILES
    file_letter = files[pos.col] if 0 <= pos.col < len(files) else str(pos.col)
    rank_number = consts.NOTATION_RANKS - pos.row
    return f"{file_letter}{rank_number}"


@dataclass(frozen=True)
class CaptureRecord:
    """One capture: the removed piece, where and when it fell, and its captor's move.

    *(captor_from, captor_to)* are the capturing movement's endpoints — the
    join key back to the move row that took this piece. The victim's *square*
    alone cannot locate that move: a collision capture happens mid-path, and
    en passant strikes the bypassed pawn's square, so neither coincides with
    the captor's destination in general.
    """

    at_ms: int
    square: str
    piece_type: str
    color: str
    captor_from: str
    captor_to: str


class CaptureLog(Observer):
    """Records every PieceCapturedEvent for later move annotation."""

    def __init__(self) -> None:
        self._records: List[CaptureRecord] = []

    def on_event(self, event: Event) -> None:
        if not isinstance(event, PieceCapturedEvent):
            return
        self._records.append(
            CaptureRecord(
                at_ms=event.at_ms,
                square=_algebraic(event.pos),
                piece_type=event.piece_type,
                color=event.color,
                captor_from=_algebraic(event.captor_frm),
                captor_to=_algebraic(event.captor_to),
            )
        )

    def records(self) -> List[CaptureRecord]:
        return list(self._records)
