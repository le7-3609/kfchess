from abc import ABC, abstractmethod
from typing import List, Tuple

from kfchess.models.board import Board
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
