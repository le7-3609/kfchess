import unittest
from io import StringIO
import sys
from kfchess.models import Board, Piece, Color, PieceType, Position
from kfchess.repository import InMemoryBoardRepository
from kfchess.services import (
    Result,
    SimpleBoardParser,
    BoardValidator,
    ConsoleBoardPrinter,
    GameService
)

class TestResult(unittest.TestCase):
    def test_ok_result(self):
        res = Result.ok("hello")
        self.assertTrue(res.is_ok)
        self.assertEqual(res.value, "hello")
        with self.assertRaises(ValueError):
            _ = res.error

    def test_fail_result(self):
        res = Result.fail("error_msg")
        self.assertFalse(res.is_ok)
        self.assertEqual(res.error, "error_msg")
        with self.assertRaises(ValueError):
            _ = res.value


class TestSimpleBoardParser(unittest.TestCase):
    def test_parse_valid(self):
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


class TestBoardValidator(unittest.TestCase):
    def test_empty_board(self):
        validator = BoardValidator()
        res = validator.validate_and_build([])
        self.assertFalse(res.is_ok)
        self.assertEqual(res.error, "EMPTY_BOARD")

    def test_row_width_mismatch(self):
        validator = BoardValidator()
        raw_board = [
            ["wK", "."],
            ["bP", ".", "."]
        ]
        res = validator.validate_and_build(raw_board)
        self.assertFalse(res.is_ok)
        self.assertEqual(res.error, "ROW_WIDTH_MISMATCH")

    def test_unknown_token(self):
        validator = BoardValidator()
        raw_board = [
            ["wK", "."],
            ["wZ", "."]
        ]
        res = validator.validate_and_build(raw_board)
        self.assertFalse(res.is_ok)
        self.assertEqual(res.error, "UNKNOWN_TOKEN")

    def test_valid_board_creation(self):
        validator = BoardValidator()
        raw_board = [
            ["wK", "."],
            [".", "bP"]
        ]
        res = validator.validate_and_build(raw_board)
        self.assertTrue(res.is_ok)
        board = res.value
        self.assertEqual(board.rows, 2)
        self.assertEqual(board.cols, 2)
        self.assertEqual(board.get_piece(Position(0, 0)), Piece(Color.WHITE, PieceType.KING))
        self.assertIsNone(board.get_piece(Position(0, 1)))


class TestConsoleBoardPrinter(unittest.TestCase):
    def test_print_board(self):
        board = Board(2, 2)
        board.set_piece(Position(0, 0), Piece(Color.WHITE, PieceType.KING))
        board.set_piece(Position(1, 1), Piece(Color.BLACK, PieceType.PAWN))

        printer = ConsoleBoardPrinter()
        old_stdout = sys.stdout
        sys.stdout = mystdout = StringIO()
        try:
            printer.print_board(board)
        finally:
            sys.stdout = old_stdout

        self.assertEqual(mystdout.getvalue(), "wK .\n. bP\n")


class TestGameService(unittest.TestCase):
    def test_execute_flow(self):
        repository = InMemoryBoardRepository()
        parser = SimpleBoardParser()
        validator = BoardValidator()
        
        # We capture print output using a mock printer or capturing stdout
        class MockPrinter(ConsoleBoardPrinter):
            def __init__(self):
                self.printed = []
            def print_board(self, board: Board):
                self.printed.append(board)

        printer = MockPrinter()
        service = GameService(repository, parser, validator, printer)

        input_data = [
            "Board:",
            "wK .",
            ". bP",
            "Commands:",
            "print board"
        ]

        res = service.execute(input_data)
        self.assertTrue(res.is_ok)
        self.assertEqual(len(printer.printed), 1)
        self.assertEqual(printer.printed[0].rows, 2)


if __name__ == '__main__':
    unittest.main()
