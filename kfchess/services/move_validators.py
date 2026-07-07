"""Per-piece movement validators (Strategy pattern).

Each concrete class encodes the geometric movement shape for one piece type.
They are stateless, board-unaware, and injected via MoveValidatorFactory.
"""

from kfchess.models.board import Position
from kfchess.models.piece import Color
from kfchess.services.interfaces import MoveValidatorInterface


class KingMoveValidator(MoveValidatorInterface):
    """King may move exactly one square in any direction."""

    def is_legal(self, frm: Position, to: Position, color: Color = Color.WHITE) -> bool:
        dr = abs(to.row - frm.row)
        dc = abs(to.col - frm.col)
        # Must move (not stay) and neither axis may exceed 1.
        return (dr, dc) != (0, 0) and dr <= 1 and dc <= 1


class RookMoveValidator(MoveValidatorInterface):
    """Rook moves any number of squares along a rank or file."""

    def is_legal(self, frm: Position, to: Position, color: Color = Color.WHITE) -> bool:
        dr = to.row - frm.row
        dc = to.col - frm.col
        # Exactly one of the deltas must be zero (straight line, non-zero).
        return (dr == 0) != (dc == 0)


class BishopMoveValidator(MoveValidatorInterface):
    """Bishop moves any number of squares diagonally."""

    def is_legal(self, frm: Position, to: Position, color: Color = Color.WHITE) -> bool:
        dr = abs(to.row - frm.row)
        dc = abs(to.col - frm.col)
        # Equal non-zero deltas ⟹ diagonal.
        return dr == dc and dr != 0


class QueenMoveValidator(MoveValidatorInterface):
    """Queen combines Rook and Bishop movement."""

    def __init__(self) -> None:
        self._rook = RookMoveValidator()
        self._bishop = BishopMoveValidator()

    def is_legal(self, frm: Position, to: Position, color: Color = Color.WHITE) -> bool:
        return self._rook.is_legal(frm, to, color) or self._bishop.is_legal(frm, to, color)


class KnightMoveValidator(MoveValidatorInterface):
    """Knight moves in an L-shape: 2 squares on one axis, 1 on the other."""

    def is_legal(self, frm: Position, to: Position, color: Color = Color.WHITE) -> bool:
        dr = abs(to.row - frm.row)
        dc = abs(to.col - frm.col)
        return {dr, dc} == {1, 2}


class PawnMoveValidator(MoveValidatorInterface):
    """Pawn moves 1 square forward (up for White, down for Black)
    or 1 square diagonally forward.
    """

    def is_legal(self, frm: Position, to: Position, color: Color = Color.WHITE) -> bool:
        # White pawns move up (decreasing row), Black pawns move down (increasing row).
        # Forward or diagonal pawn move shape must be exactly 1 step in the row direction.
        row_diff = to.row - frm.row
        col_diff = abs(to.col - frm.col)

        expected_row_diff = -1 if color == Color.WHITE else 1
        return row_diff == expected_row_diff and col_diff <= 1

