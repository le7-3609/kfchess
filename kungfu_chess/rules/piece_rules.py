"""Per-piece movement rules (Layer 2).

Each concrete validator encodes the geometric movement shape for one piece type.
Validators are stateless and board-unaware.

Also contains:
  - MoveValidatorFactory  — maps piece_type str -> MoveValidatorInterface
  - PromotionStrategyInterface / StandardPawnPromotion
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

import consts
from kungfu_chess.errors import MissingValidatorError
from kungfu_chess.model.position import Position
from kungfu_chess.model.piece import PieceInterface


# ---------------------------------------------------------------------------
# Config dependency (minimal — only pawn start rows & forward direction)
# ---------------------------------------------------------------------------

# We import GameConfig lazily via type hints only, to avoid circular imports.
# Validators that need player config accept it directly in __init__.


# ---------------------------------------------------------------------------
# MoveValidator interface
# ---------------------------------------------------------------------------

class MoveValidatorInterface(ABC):
    """Decides whether a move from *frm* to *to* is geometrically legal."""

    @abstractmethod
    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        """Return True iff the move shape is valid for this piece type."""

    @abstractmethod
    def get_candidate_targets(self, frm: Position, color: str, board_rows: int, board_cols: int) -> List[Position]:
        """Generate all candidate positions that this piece type can geometrically reach from *frm*."""


# ---------------------------------------------------------------------------
# Concrete piece validators
# ---------------------------------------------------------------------------

class KingMoveValidator(MoveValidatorInterface):
    """King may move exactly one square in any direction."""

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        dr = abs(to.row - frm.row)
        dc = abs(to.col - frm.col)
        return (dr, dc) != (0, 0) and dr <= 1 and dc <= 1

    def get_candidate_targets(self, frm: Position, color: str, board_rows: int, board_cols: int) -> List[Position]:
        targets = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                r, c = frm.row + dr, frm.col + dc
                if 0 <= r < board_rows and 0 <= c < board_cols:
                    targets.append(Position(r, c))
        return targets


class RookMoveValidator(MoveValidatorInterface):
    """Rook moves any number of squares along a rank or file."""

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        dr = to.row - frm.row
        dc = to.col - frm.col
        return (dr == 0) != (dc == 0)

    def get_candidate_targets(self, frm: Position, color: str, board_rows: int, board_cols: int) -> List[Position]:
        targets = []
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            for step in range(1, max(board_rows, board_cols)):
                r, c = frm.row + dr * step, frm.col + dc * step
                if 0 <= r < board_rows and 0 <= c < board_cols:
                    targets.append(Position(r, c))
                else:
                    break
        return targets


class BishopMoveValidator(MoveValidatorInterface):
    """Bishop moves any number of squares diagonally."""

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        dr = abs(to.row - frm.row)
        dc = abs(to.col - frm.col)
        return dr == dc and dr != 0

    def get_candidate_targets(self, frm: Position, color: str, board_rows: int, board_cols: int) -> List[Position]:
        targets = []
        for dr, dc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
            for step in range(1, max(board_rows, board_cols)):
                r, c = frm.row + dr * step, frm.col + dc * step
                if 0 <= r < board_rows and 0 <= c < board_cols:
                    targets.append(Position(r, c))
                else:
                    break
        return targets


class QueenMoveValidator(MoveValidatorInterface):
    """Queen combines Rook and Bishop movement."""

    def __init__(self) -> None:
        self._rook = RookMoveValidator()
        self._bishop = BishopMoveValidator()

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        return self._rook.is_legal(frm, to, color, board_rows) or self._bishop.is_legal(frm, to, color, board_rows)

    def get_candidate_targets(self, frm: Position, color: str, board_rows: int, board_cols: int) -> List[Position]:
        return (self._rook.get_candidate_targets(frm, color, board_rows, board_cols) +
                self._bishop.get_candidate_targets(frm, color, board_rows, board_cols))


class KnightMoveValidator(MoveValidatorInterface):
    """Knight moves in an L-shape: 2 squares on one axis, 1 on the other."""

    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        dr = abs(to.row - frm.row)
        dc = abs(to.col - frm.col)
        return {dr, dc} == {1, 2}

    def get_candidate_targets(self, frm: Position, color: str, board_rows: int, board_cols: int) -> List[Position]:
        targets = []
        for dr, dc in ((-2,-1), (-2,1), (-1,-2), (-1,2), (1,-2), (1,2), (2,-1), (2,1)):
            r, c = frm.row + dr, frm.col + dc
            if 0 <= r < board_rows and 0 <= c < board_cols:
                targets.append(Position(r, c))
        return targets


class PawnMoveValidator(MoveValidatorInterface):
    """Pawn moves 1 square forward (or 2 from start row), or 1 square diagonally forward.

    Requires a GameConfig to determine forward direction and start rows per player.
    """

    def __init__(self, config: 'GameConfig') -> None:  # type: ignore[name-defined]
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

    def get_candidate_targets(self, frm: Position, color: str, board_rows: int, board_cols: int) -> List[Position]:
        targets = []
        player_config = self._config.get_player(color)
        if not player_config:
            return targets
        direction = player_config.forward_direction
        for dr in (direction, direction * 2):
            for dc in (-1, 0, 1):
                r, c = frm.row + dr, frm.col + dc
                if 0 <= r < board_rows and 0 <= c < board_cols:
                    if self.is_legal(frm, Position(r, c), color, board_rows):
                        targets.append(Position(r, c))
        return targets


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class MoveValidatorFactoryInterface(ABC):
    """Creates (or retrieves) the correct MoveValidatorInterface for a piece."""

    @abstractmethod
    def get_validator(self, piece_type: str) -> MoveValidatorInterface:
        """Return the MoveValidatorInterface instance for *piece_type*."""


class MoveValidatorFactory(MoveValidatorFactoryInterface):
    """Simple dict-based factory (Strategy + Factory pattern)."""

    def __init__(self, validators: Dict[str, MoveValidatorInterface]) -> None:
        self._validators = validators

    def get_validator(self, piece_type: str) -> MoveValidatorInterface:
        validator = self._validators.get(piece_type)
        if validator is None:
            raise MissingValidatorError(piece_type)
        return validator


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------

class PromotionStrategyInterface(ABC):
    """Abstract interface for piece promotion rules."""

    @abstractmethod
    def evaluate_promotion(self, piece: PieceInterface, to_pos: Position, config: 'GameConfig') -> None:  # type: ignore[name-defined]
        """Evaluate and apply any promotion rules to *piece* after it moves to *to_pos*."""


class StandardPawnPromotion(PromotionStrategyInterface):
    """Auto-promotes a pawn to queen when it reaches the opposite back rank."""

    def evaluate_promotion(self, piece: PieceInterface, to_pos: Position, config: 'GameConfig') -> None:  # type: ignore[name-defined]
        if piece.piece_type != "P":
            return
        player_config = config.get_player(piece.color)
        if player_config is None:
            return
        # Determine the promotion rank from the player config.
        if to_pos.row == player_config.promotion_rank:
            piece.piece_type = consts.DEFAULT_PROMOTION_PIECE  # type: ignore[misc]
