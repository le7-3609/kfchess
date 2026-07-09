from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from kfchess.models.interfaces import BoardInterface, PieceInterface
from kfchess.models.board import Position
from kfchess.models.result import Result
from kfchess.models.game_state import GameState


class BoardParserInterface(ABC):
    @abstractmethod
    def parse(self, input_lines: List[str]) -> Tuple[List[List[str]], List[str]]:
        """Parse raw input lines into (board_token_rows, command_strings)."""


class BoardValidatorInterface(ABC):
    @abstractmethod
    def validate_and_build(self, raw_board: List[List[str]]) -> 'Result[BoardInterface, str]':
        """Validate raw token rows and build a Board on success."""


class BoardPrinterInterface(ABC):
    @abstractmethod
    def print_board(self, board: BoardInterface) -> None:
        """Write the board layout to the output stream."""


class CommandExecutorInterface(ABC):
    @abstractmethod
    def execute_command(self, command: str) -> None:
        """Execute a single text command against the current game state."""





# ---------------------------------------------------------------------------
# Observer pattern — move events
# ---------------------------------------------------------------------------

class MoveEventListener(ABC):
    """Observer that is notified whenever a piece is successfully moved."""

    @abstractmethod
    def on_move(self, piece: PieceInterface, frm: Position, to: Position) -> None:
        """Called after a legal move has been committed to the board."""





# ---------------------------------------------------------------------------
# Real-time Movement over Time
# ---------------------------------------------------------------------------

class MovementDurationInterface(ABC):
    """Calculates the travel duration for a piece moving between positions."""

    @abstractmethod
    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        """Return the travel duration in milliseconds."""


class MovementManagerInterface(ABC):
    """Manages active movements in transit, resolves arrivals, and updates the board."""

    @abstractmethod
    def calculate_arrival(self, frm: Position, to: Position, piece: PieceInterface, start_ms: int) -> int:
        """Return the arrival timestamp in milliseconds."""

    @abstractmethod
    def resolve_movements(self, board: BoardInterface, state: GameState, current_ms: int) -> None:
        """Update the board with any pieces that have finished transit by current_ms."""

    @abstractmethod
    def get_effective_board(self, board: BoardInterface, state: GameState, t: int) -> BoardInterface:
        """Return a BoardInterface containing the locations of all pieces at time t."""

