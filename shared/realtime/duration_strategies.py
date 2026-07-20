"""Movement duration strategies (Layer 4).

Strategy pattern for computing how long a piece takes to travel between squares.
"""

from abc import ABC, abstractmethod

from shared.config import consts
from shared.model.position import Position
from shared.model.piece import PieceInterface


class MovementDurationInterface(ABC):
    """Calculates the travel duration for a piece moving between two positions."""

    @abstractmethod
    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        """Return the travel duration in milliseconds."""


class InstantMovementDuration(MovementDurationInterface):
    """All movements are instant (0 ms duration)."""

    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        return consts.INSTANT_DURATION_MS


class ChebyshevDistanceDuration(MovementDurationInterface):
    """Duration is proportional to the Chebyshev distance between squares."""

    def __init__(self, ms_per_square: int = consts.DEFAULT_MS_PER_SQUARE) -> None:
        self._ms_per_square = ms_per_square

    def set_ms_per_square(self, ms_per_square: int) -> None:
        self._ms_per_square = ms_per_square

    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        dist = max(abs(to.row - frm.row), abs(to.col - frm.col))
        return dist * self._ms_per_square
