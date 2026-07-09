"""Per-piece movement validators (Strategy pattern).

Each concrete class encodes the geometric movement shape for one piece type.
They are stateless, board-unaware, and injected via MoveValidatorFactory.
"""

from typing import Optional
from kfchess.models.board import Position
from kfchess.rules.interfaces import MoveValidatorInterface
from kfchess.config.game_config import GameConfig


class KingMoveValidator(MoveValidatorInterface):
    """King may move exactly one square in any direction."""

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        dr = abs(to.row - frm.row)
        dc = abs(to.col - frm.col)
        # Must move (not stay) and neither axis may exceed 1.
        return (dr, dc) != (0, 0) and dr <= 1 and dc <= 1


class RookMoveValidator(MoveValidatorInterface):
    """Rook moves any number of squares along a rank or file."""

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        dr = to.row - frm.row
        dc = to.col - frm.col
        # Exactly one of the deltas must be zero (straight line, non-zero).
        return (dr == 0) != (dc == 0)


class BishopMoveValidator(MoveValidatorInterface):
    """Bishop moves any number of squares diagonally."""

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        dr = abs(to.row - frm.row)
        dc = abs(to.col - frm.col)
        # Equal non-zero deltas ⟹ diagonal.
        return dr == dc and dr != 0


class QueenMoveValidator(MoveValidatorInterface):
    """Queen combines Rook and Bishop movement."""

    def __init__(self) -> None:
        self._rook = RookMoveValidator()
        self._bishop = BishopMoveValidator()

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        return self._rook.is_legal(frm, to, color, board_rows) or self._bishop.is_legal(frm, to, color, board_rows)


class KnightMoveValidator(MoveValidatorInterface):
    """Knight moves in an L-shape: 2 squares on one axis, 1 on the other."""

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        dr = abs(to.row - frm.row)
        dc = abs(to.col - frm.col)
        return {dr, dc} == {1, 2}


class PawnMoveValidator(MoveValidatorInterface):
    """Pawn moves 1 square forward, 1 square diagonally forward, 
    or 2 squares forward from its start row, dynamically driven by GameConfig.
    """

    def __init__(self, config: GameConfig) -> None:
        self._config = config

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        player_config = self._config.get_player(color)
        if not player_config:
            return False

        row_diff = to.row - frm.row
        col_diff = abs(to.col - frm.col)

        expected_row_diff = player_config.forward_direction

        # 1-step forward or diagonal
        if row_diff == expected_row_diff and col_diff <= 1:
            return True

        # 2-step forward from start row
        if frm.row in player_config.pawn_start_rows and row_diff == expected_row_diff * 2 and col_diff == 0:
            return True

        return False
