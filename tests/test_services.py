import sys
import unittest
from io import StringIO

from kfchess.models.board import Board, Position
from kfchess.models.game_state import GameState
from kfchess.models.piece import Color, Piece, PieceType
from kfchess.models.result import Result
from kfchess.repositories.in_memory import InMemoryBoardrepositories, InMemoryGameStaterepositories
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.move_validator_factory import MoveValidatorFactory
from kfchess.services.parser import SimpleBoardParser
from kfchess.services.printer import ConsoleBoardPrinter
from kfchess.services.validator import BoardValidator
from kfchess.services.game_service import GameService


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

class TestResult(unittest.TestCase):
    def test_ok_result(self) -> None:
        res = Result.ok("hello")
        self.assertTrue(res.is_ok)
        self.assertEqual(res.value, "hello")
        with self.assertRaises(ValueError):
            _ = res.error

    def test_fail_result(self) -> None:
        res = Result.fail("error_msg")
        self.assertFalse(res.is_ok)
        self.assertEqual(res.error, "error_msg")
        with self.assertRaises(ValueError):
            _ = res.value


# ---------------------------------------------------------------------------
# SimpleBoardParser
# ---------------------------------------------------------------------------

class TestSimpleBoardParser(unittest.TestCase):
    def test_parse_valid(self) -> None:
        input_data = [
            "  ",
            "Board:",
            "wK . .",
            " . bP .",
            "Commands:",
            "print board",
            "  ",
        ]
        parser = SimpleBoardParser()
        raw_board, commands = parser.parse(input_data)
        self.assertEqual(raw_board, [["wK", ".", "."], [".", "bP", "."]])
        self.assertEqual(commands, ["print board"])


# ---------------------------------------------------------------------------
# BoardValidator
# ---------------------------------------------------------------------------

class TestBoardValidator(unittest.TestCase):
    def test_empty_board(self) -> None:
        res = BoardValidator().validate_and_build([])
        self.assertFalse(res.is_ok)
        self.assertEqual(res.error, "EMPTY_BOARD")

    def test_row_width_mismatch(self) -> None:
        res = BoardValidator().validate_and_build([["wK", "."], ["bP", ".", "."]])
        self.assertFalse(res.is_ok)
        self.assertEqual(res.error, "ROW_WIDTH_MISMATCH")

    def test_unknown_token(self) -> None:
        res = BoardValidator().validate_and_build([["wK", "."], ["wZ", "."]])
        self.assertFalse(res.is_ok)
        self.assertEqual(res.error, "UNKNOWN_TOKEN")

    def test_valid_board_creation(self) -> None:
        res = BoardValidator().validate_and_build([["wK", "."], [".", "bP"]])
        self.assertTrue(res.is_ok)
        board = res.value
        self.assertEqual(board.rows, 2)
        self.assertEqual(board.cols, 2)
        self.assertEqual(board.get_piece(Position(0, 0)), Piece(Color.WHITE, PieceType.KING))
        self.assertIsNone(board.get_piece(Position(0, 1)))


# ---------------------------------------------------------------------------
# ConsoleBoardPrinter
# ---------------------------------------------------------------------------

class TestConsoleBoardPrinter(unittest.TestCase):
    def test_print_board(self) -> None:
        board = Board(2, 2)
        board.set_piece(Position(0, 0), Piece(Color.WHITE, PieceType.KING))
        board.set_piece(Position(1, 1), Piece(Color.BLACK, PieceType.PAWN))

        printer = ConsoleBoardPrinter()
        old_stdout, sys.stdout = sys.stdout, StringIO()
        try:
            printer.print_board(board)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertEqual(output, "wK .\n. bP\n")


# ---------------------------------------------------------------------------
# CommandExecutor
# ---------------------------------------------------------------------------

def _make_executor(board: Board) -> tuple[CommandExecutor, InMemoryBoardrepositories,
                                          InMemoryGameStaterepositories]:
    """Helper: load a board into fresh repos and return a wired CommandExecutor."""
    board_repo = InMemoryBoardrepositories()
    state_repo = InMemoryGameStaterepositories()
    board_repo.save_board(board)
    state_repo.save_state(GameState())

    class _NullPrinter(ConsoleBoardPrinter):
        """Discard output — print tests are handled separately."""
        def print_board(self, board: Board) -> None:  # type: ignore[override]
            pass

    executor = CommandExecutor(
        board_repo,
        state_repo,
        _NullPrinter(),
        move_validator_factory=MoveValidatorFactory(),
        move_event_publisher=MoveEventPublisher(),
    )
    return executor, board_repo, state_repo


class TestCommandExecutor(unittest.TestCase):
    # ── click: coordinate conversion ────────────────────────────────

    def test_click_outside_board_is_ignored(self) -> None:
        """Pixel coordinates that map to a cell outside the board are ignored."""
        board = Board(2, 2)
        board.set_piece(Position(0, 0), Piece(Color.WHITE, PieceType.KING))
        executor, _, state_repo = _make_executor(board)

        executor.execute_command("click 300 0")   # col=3 — outside 2-col board
        self.assertIsNone(state_repo.get_state().selected_pos)

    def test_click_on_piece_selects_it(self) -> None:
        """Clicking cell (0,0) — pixel (50,50) — selects the piece there."""
        board = Board(2, 2)
        board.set_piece(Position(0, 0), Piece(Color.WHITE, PieceType.KING))
        executor, _, state_repo = _make_executor(board)

        executor.execute_command("click 50 50")   # row=0, col=0
        self.assertEqual(state_repo.get_state().selected_pos, Position(0, 0))

    def test_click_empty_with_no_selection_is_ignored(self) -> None:
        """Clicking an empty cell when nothing is selected does nothing."""
        board = Board(2, 2)
        executor, _, state_repo = _make_executor(board)

        executor.execute_command("click 50 50")   # empty cell
        self.assertIsNone(state_repo.get_state().selected_pos)

    def test_click_friendly_replaces_selection(self) -> None:
        """Clicking a second friendly piece replaces the current selection."""
        board = Board(1, 2)
        board.set_piece(Position(0, 0), Piece(Color.WHITE, PieceType.KING))
        board.set_piece(Position(0, 1), Piece(Color.WHITE, PieceType.ROOK))
        executor, _, state_repo = _make_executor(board)

        executor.execute_command("click 50 50")    # select (0,0)
        self.assertEqual(state_repo.get_state().selected_pos, Position(0, 0))

        executor.execute_command("click 150 50")   # click (0,1) — also White
        self.assertEqual(state_repo.get_state().selected_pos, Position(0, 1))

    def test_click_empty_cell_with_selection_moves_piece(self) -> None:
        """Clicking an empty cell while a piece is selected moves it there."""
        board = Board(1, 2)
        board.set_piece(Position(0, 0), Piece(Color.WHITE, PieceType.KING))
        executor, board_repo, state_repo = _make_executor(board)

        executor.execute_command("click 50 50")    # select (0,0)
        executor.execute_command("click 150 50")   # move to (0,1) — empty

        updated_board = board_repo.get_board()
        assert updated_board is not None
        self.assertIsNone(updated_board.get_piece(Position(0, 0)))
        self.assertEqual(
            updated_board.get_piece(Position(0, 1)),
            Piece(Color.WHITE, PieceType.KING),
        )
        self.assertIsNone(state_repo.get_state().selected_pos)

    def test_click_opponent_cell_with_selection_captures(self) -> None:
        """Moving to an opponent's cell captures (overwrites) the opponent."""
        board = Board(1, 2)
        board.set_piece(Position(0, 0), Piece(Color.WHITE, PieceType.KING))
        board.set_piece(Position(0, 1), Piece(Color.BLACK, PieceType.PAWN))
        executor, board_repo, state_repo = _make_executor(board)

        executor.execute_command("click 50 50")    # select white King
        executor.execute_command("click 150 50")   # capture black Pawn

        updated_board = board_repo.get_board()
        assert updated_board is not None
        self.assertIsNone(updated_board.get_piece(Position(0, 0)))
        self.assertEqual(
            updated_board.get_piece(Position(0, 1)),
            Piece(Color.WHITE, PieceType.KING),
        )
        self.assertIsNone(state_repo.get_state().selected_pos)

    # ── wait ────────────────────────────────────────────────────────

    def test_wait_advances_clock(self) -> None:
        board = Board(1, 1)
        executor, _, state_repo = _make_executor(board)

        executor.execute_command("wait 500")
        self.assertEqual(state_repo.get_state().clock_ms, 500)

        executor.execute_command("wait 1000")
        self.assertEqual(state_repo.get_state().clock_ms, 1500)

    # ── print board (smoke test) ────────────────────────────────────

    def test_print_board_command(self) -> None:
        """print board writes the expected board string to stdout."""
        board = Board(1, 2)
        board.set_piece(Position(0, 0), Piece(Color.WHITE, PieceType.KING))
        board_repo = InMemoryBoardrepositories()
        state_repo = InMemoryGameStaterepositories()
        board_repo.save_board(board)
        state_repo.save_state(GameState())
        printer = ConsoleBoardPrinter()
        executor = CommandExecutor(
            board_repo,
            state_repo,
            printer,
            move_validator_factory=MoveValidatorFactory(),
            move_event_publisher=MoveEventPublisher(),
        )

        old_stdout, sys.stdout = sys.stdout, StringIO()
        try:
            executor.execute_command("print board")
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        self.assertEqual(output, "wK .\n")


# ---------------------------------------------------------------------------
# GameService
# ---------------------------------------------------------------------------

class TestGameService(unittest.TestCase):
    def _build_service(self) -> tuple[GameService, InMemoryBoardrepositories,
                                       InMemoryGameStaterepositories]:
        board_repo = InMemoryBoardrepositories()
        state_repo = InMemoryGameStaterepositories()
        parser = SimpleBoardParser()
        validator = BoardValidator()

        class _NullPrinter(ConsoleBoardPrinter):
            def print_board(self, board: Board) -> None:  # type: ignore[override]
                pass

        printer = _NullPrinter()
        executor = CommandExecutor(
            board_repo,
            state_repo,
            printer,
            move_validator_factory=MoveValidatorFactory(),
            move_event_publisher=MoveEventPublisher(),
        )
        service = GameService(board_repo, state_repo, parser, validator, executor)
        return service, board_repo, state_repo

    def test_execute_sets_board(self) -> None:
        service, board_repo, _ = self._build_service()
        res = service.execute(["Board:", "wK .", ". bP", "Commands:"])
        self.assertTrue(res.is_ok)
        board = board_repo.get_board()
        self.assertIsNotNone(board)
        assert board is not None
        self.assertEqual(board.rows, 2)

    def test_execute_returns_error_on_invalid_board(self) -> None:
        service, _, _ = self._build_service()
        res = service.execute(["Board:", "wK .", ". bZ", "Commands:"])
        self.assertFalse(res.is_ok)
        self.assertEqual(res.error, "UNKNOWN_TOKEN")


if __name__ == '__main__':
    unittest.main()
