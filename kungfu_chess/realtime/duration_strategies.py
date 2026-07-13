"""Movement duration strategies (Layer 4).

Strategy pattern for computing how long a piece takes to travel between squares.
"""

from abc import ABC, abstractmethod

from kungfu_chess.model.position import Position
from kungfu_chess.model.piece import PieceInterface


class MovementDurationInterface(ABC):
    """Calculates the travel duration for a piece moving between two positions."""

    @abstractmethod
    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        """Return the travel duration in milliseconds."""


class InstantMovementDuration(MovementDurationInterface):
    """All movements are instant (0 ms duration)."""

    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        return 0


class ChebyshevDistanceDuration(MovementDurationInterface):
    """Duration is proportional to the Chebyshev distance between squares."""

    def __init__(self, ms_per_square: int = 1000) -> None:
        self._ms_per_square = ms_per_square

    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        dist = max(abs(to.row - frm.row), abs(to.col - frm.col))
        return dist * self._ms_per_square
