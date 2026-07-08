"""Move event publisher (Observer pattern).

MoveEventPublisher is the *subject*; any number of MoveEventListener
implementations can subscribe and be notified whenever a legal move is
committed to the board.

Typical usage in this iteration: no listeners are registered by default,
but the infrastructure is ready for future consumers (sound, animation,
move history, etc.).
"""

from typing import List

from kfchess.models.board import Position
from kfchess.models.piece import TextPiece as Piece, PieceFactory
from kfchess.services.interfaces import MoveEventListener


class MoveEventPublisher:
    """Manages subscriptions and dispatches move events to all listeners."""

    def __init__(self) -> None:
        self._listeners: List[MoveEventListener] = []

    def subscribe(self, listener: MoveEventListener) -> None:
        """Register *listener* to receive future move events."""
        self._listeners.append(listener)

    def unsubscribe(self, listener: MoveEventListener) -> None:
        """Remove *listener*; silently ignored if not currently subscribed."""
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def publish(self, piece: Piece, frm: Position, to: Position) -> None:
        """Notify all registered listeners that *piece* moved from *frm* to *to*."""
        for listener in self._listeners:
            listener.on_move(piece, frm, to)
