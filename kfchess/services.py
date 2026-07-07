import sys
from abc import ABC, abstractmethod
from typing import List, Tuple, Optional, Generic, TypeVar
from kfchess.models import Board, Piece, Position, Color, PieceType
from kfchess.repository import BoardRepositoryInterface

T = TypeVar('T')
E = TypeVar('E')

class Result(Generic[T, E]):
    """A clean value-returning structure for success/failure handling."""
    def __init__(self, is_ok: bool, value: Optional[T] = None, error: Optional[E] = None):
        self.is_ok = is_ok
        self._value = value
        self._error = error

    @property
    def value(self) -> T:
        if not self.is_ok:
            raise ValueError(f"Cannot retrieve value from a failed Result: {self._error}")
        return self._value

    @property
    def error(self) -> E:
        if self.is_ok:
            raise ValueError("Cannot retrieve error from a successful Result")
        return self._error

    @classmethod
    def ok(cls, value: T) -> 'Result[T, E]':
        return cls(is_ok=True, value=value)

    @classmethod
    def fail(cls, error: E) -> 'Result[T, E]':
        return cls(is_ok=False, error=error)


class BoardParserInterface(ABC):
    @abstractmethod
    def parse(self, input_lines: List[str]) -> Tuple[List[List[str]], List[str]]:
        """Parses lines into a raw list of token lists (board cells) and commands."""
        pass


class SimpleBoardParser(BoardParserInterface):
    def parse(self, input_lines: List[str]) -> Tuple[List[List[str]], List[str]]:
        board_lines = []
        commands = []
        in_board = False
        in_commands = False

        for line in input_lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue

            if line_stripped.startswith("Board:"):
                in_board = True
                in_commands = False
                continue
            elif line_stripped.startswith("Commands:"):
                in_board = False
                in_commands = True
                continue

            if in_board:
                tokens = line_stripped.split()
                if tokens:
                    board_lines.append(tokens)
            elif in_commands:
                commands.append(line_stripped)

        return board_lines, commands


class BoardValidatorInterface(ABC):
    @abstractmethod
    def validate_and_build(self, raw_board: List[List[str]]) -> Result[Board, str]:
        """Validates the structure and tokens of the board, returning a Board object on success."""
        pass


class BoardValidator(BoardValidatorInterface):
    def validate_and_build(self, raw_board: List[List[str]]) -> Result[Board, str]:
        if not raw_board:
            return Result.fail("EMPTY_BOARD")

        expected_width = len(raw_board[0])
        for row_idx, row in enumerate(raw_board):
            if len(row) != expected_width:
                return Result.fail("ROW_WIDTH_MISMATCH")

            for col_idx, token in enumerate(row):
                if token == '.':
                    continue
                piece = Piece.from_string(token)
                if piece is None:
                    return Result.fail("UNKNOWN_TOKEN")

        # Build board entity
        rows = len(raw_board)
        cols = expected_width
        board = Board(rows, cols)
        for r_idx, row in enumerate(raw_board):
            for c_idx, token in enumerate(row):
                if token != '.':
                    board.set_piece(Position(r_idx, c_idx), Piece.from_string(token))

        return Result.ok(board)


class BoardPrinterInterface(ABC):
    @abstractmethod
    def print_board(self, board: Board) -> None:
        """Prints the board layout to stdout or another stream."""
        pass


class ConsoleBoardPrinter(BoardPrinterInterface):
    def print_board(self, board: Board) -> None:
        for r in range(board.rows):
            sys.stdout.write(" ".join(board.get_row_tokens(r)) + "\n")


class GameService:
    def __init__(
        self,
        repository: BoardRepositoryInterface,
        parser: BoardParserInterface,
        validator: BoardValidatorInterface,
        printer: BoardPrinterInterface
    ):
        self.repository = repository
        self.parser = parser
        self.validator = validator
        self.printer = printer

    def execute(self, input_lines: List[str]) -> Result[None, str]:
        # Parse inputs
        raw_board, commands = self.parser.parse(input_lines)
        if not raw_board:
            return Result.ok(None)

        # Validate board layout
        validation_result = self.validator.validate_and_build(raw_board)
        if not validation_result.is_ok:
            return Result.fail(validation_result.error)

        board = validation_result.value
        self.repository.save_board(board)

        # Run commands
        for cmd in commands:
            if cmd == "print board":
                stored_board = self.repository.get_board()
                if stored_board:
                    self.printer.print_board(stored_board)

        return Result.ok(None)
