from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from kfchess.models.board import Position
from kfchess.models.interfaces import PieceInterface, BoardInterface
from kfchess.config.game_config import GameConfig

class MoveValidatorInterface(ABC):
    """Decides whether a move from *frm* to *to* is geometrically legal."""

    @abstractmethod
    def is_legal(self, frm: Position, to: Position, color: str = "w", board_rows: int = 8) -> bool:
        """Return True iff the move shape is valid for this piece type."""


class MoveValidatorFactoryInterface(ABC):
    """Creates (or retrieves) the correct MoveValidatorInterface for a piece."""

    @abstractmethod
    def get_validator(self, piece_type: str) -> MoveValidatorInterface:
        """Return the MoveValidatorInterface instance for *piece_type*."""


class PathCheckerInterface(ABC):
    """Board-aware validator for path-blocking and capture legality."""

    @abstractmethod
    def is_path_clear(
        self,
        board: BoardInterface,
        frm: Position,
        to: Position,
    ) -> bool:
        """Return True if every intermediate square between *frm* and *to* is empty."""

    @abstractmethod
    def can_land(
        self,
        board: BoardInterface,
        moving_piece: PieceInterface,
        frm: Position,
        to: Position,
        en_passant_targets: Optional[List[Position]] = None,
    ) -> bool:
        """Return True if *moving_piece* is allowed to land on square *to*."""

class PromotionStrategyInterface(ABC):
    """Abstract interface for piece promotion rules."""
    @abstractmethod
    def evaluate_promotion(self, piece: PieceInterface, to_pos: Position, config: GameConfig) -> None:
        """Evaluate and apply any promotion rules to the piece after moving to `to_pos`."""
