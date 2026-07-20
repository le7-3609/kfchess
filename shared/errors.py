"""Custom exception hierarchy for Kung Fu Chess.

Every error the engine raises deliberately should derive from
KungFuChessError so callers can catch the whole family, or a specific
subclass, instead of matching on message text.
"""

from shared.model.position import Position


class KungFuChessError(Exception):
    """Base class for all Kung Fu Chess errors."""


class InvalidPositionError(KungFuChessError):
    """Raised when a board is accessed at a position outside its bounds."""

    def __init__(self, pos: Position) -> None:
        self.pos = pos
        super().__init__(f"Position out of board bounds: {pos!r}")


class MissingValidatorError(KungFuChessError):
    """Raised when no MoveValidator is registered for a piece type."""

    def __init__(self, piece_type: str) -> None:
        self.piece_type = piece_type
        super().__init__(f"No move validator registered for piece type '{piece_type}'.")


class ResultAccessError(KungFuChessError):
    """Raised when a Result's value/error is accessed on the wrong branch."""


class OccupiedCellError(KungFuChessError):
    """Raised when adding a piece to a cell that is already occupied."""

    def __init__(self, pos: Position) -> None:
        self.pos = pos
        super().__init__(f"Cell already occupied: {pos!r}")


class EmptyCellError(KungFuChessError):
    """Raised when moving a piece from a cell that has no piece on it."""

    def __init__(self, pos: Position) -> None:
        self.pos = pos
        super().__init__(f"No piece to move from: {pos!r}")
