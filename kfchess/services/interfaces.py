from abc import ABC, abstractmethod
from typing import List, Tuple

from kfchess.models.board import Board, Position
from kfchess.models.piece import Piece, PieceType
from kfchess.models.result import Result


class BoardParserInterface(ABC):
    @abstractmethod
    def parse(self, input_lines: List[str]) -> Tuple[List[List[str]], List[str]]:
        """Parse raw input lines into (board_token_rows, command_strings)."""


class BoardValidatorInterface(ABC):
    @abstractmethod
    def validate_and_build(self, raw_board: List[List[str]]) -> 'Result[Board, str]':
        """Validate raw token rows and build a Board on success."""


class BoardPrinterInterface(ABC):
    @abstractmethod
    def print_board(self, board: Board) -> None:
        """Write the board layout to the output stream."""


class CommandExecutorInterface(ABC):
    @abstractmethod
    def execute_command(self, command: str) -> None:
        """Execute a single text command against the current game state."""


# ---------------------------------------------------------------------------
# Strategy pattern — per-piece movement rules
# ---------------------------------------------------------------------------

class MoveValidatorInterface(ABC):
    """Decides whether a move from *frm* to *to* is geometrically legal.

    Implementations encode the movement shape for a single piece type.
    They are stateless and board-unaware (no path-blocking in this iteration).
    """

    @abstractmethod
    def is_legal(self, frm: Position, to: Position) -> bool:
        """Return True iff the move shape is valid for this piece type."""


# ---------------------------------------------------------------------------
# Factory pattern — maps PieceType → MoveValidatorInterface
# ---------------------------------------------------------------------------

class MoveValidatorFactoryInterface(ABC):
    """Creates (or retrieves) the correct MoveValidatorInterface for a piece."""

    @abstractmethod
    def get_validator(self, piece_type: PieceType) -> MoveValidatorInterface:
        """Return the MoveValidatorInterface instance for *piece_type*."""


# ---------------------------------------------------------------------------
# Observer pattern — move events
# ---------------------------------------------------------------------------

class MoveEventListener(ABC):
    """Observer that is notified whenever a piece is successfully moved."""

    @abstractmethod
    def on_move(self, piece: Piece, frm: Position, to: Position) -> None:
        """Called after a legal move has been committed to the board."""
