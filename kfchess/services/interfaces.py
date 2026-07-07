from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from kfchess.models.board import Board, Position
from kfchess.models.piece import Color, Piece, PieceType
from kfchess.models.result import Result
from kfchess.models.game_state import GameState


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

    Implementations encode the movement *shape* for a single piece type.
    They are **stateless and board-unaware** — they know nothing about other
    pieces on the board.  Path-blocking and capture legality are handled by
    PathCheckerInterface.
    """

    @abstractmethod
    def is_legal(self, frm: Position, to: Position, color: Color = Color.WHITE) -> bool:
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


# ---------------------------------------------------------------------------
# Strategy pattern — board-aware path and capture checks
# ---------------------------------------------------------------------------

class PathCheckerInterface(ABC):
    """Board-aware validator for path-blocking and capture legality.

    This is a separate Strategy from MoveValidatorInterface.  Geometry is
    checked first (is the shape valid?); only then does the path checker
    examine the actual board state (is the path clear? can we land there?).
    """

    @abstractmethod
    def is_path_clear(
        self,
        board: Board,
        frm: Position,
        to: Position,
    ) -> bool:
        """Return True if every intermediate square between *frm* and *to* is empty.

        *frm* and *to* themselves are **excluded** from the check.
        Non-sliding pieces (Knight, King) always return True because they are
        never blocked by intervening pieces.
        """

    @abstractmethod
    def can_land(
        self,
        board: Board,
        moving_piece: Piece,
        frm: Position,
        to: Position,
    ) -> bool:
        """Return True if *moving_piece* is allowed to land on square *to*.

        A piece may **not** land on a square occupied by a friendly piece.
        It **may** land on an empty square or a square occupied by an enemy
        (capture).
        """


# ---------------------------------------------------------------------------
# Real-time Movement over Time
# ---------------------------------------------------------------------------

class MovementDurationInterface(ABC):
    """Calculates the travel duration for a piece moving between positions."""

    @abstractmethod
    def calculate_duration(self, frm: Position, to: Position, piece: Piece) -> int:
        """Return the travel duration in milliseconds."""


class MovementManagerInterface(ABC):
    """Manages active movements in transit, resolves arrivals, and updates the board."""

    @abstractmethod
    def calculate_arrival(self, frm: Position, to: Position, piece: Piece, start_ms: int) -> int:
        """Return the arrival timestamp in milliseconds."""

    @abstractmethod
    def resolve_movements(self, board: Board, state: GameState, current_ms: int) -> None:
        """Update the board with any pieces that have finished transit by current_ms."""

    @abstractmethod
    def get_effective_board(self, board: Board, state: GameState, t: int) -> Board:
        """Return a Board containing the locations of all pieces at time t, accounting for in-transit pieces."""

