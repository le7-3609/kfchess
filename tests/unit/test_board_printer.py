"""Unit tests for kungfu_chess.io.board_printer."""

import sys
import unittest
from io import StringIO

from kungfu_chess.model.board import ArrayBoard as Board
from kungfu_chess.model.piece import TextPiece as Piece
from kungfu_chess.model.position import Position
from kungfu_chess.io.board_printer import BoardPrinter


class TestBoardPrinter(unittest.TestCase):

    def setUp(self) -> None:
        self.printer = BoardPrinter()

    def _capture(self, board: Board) -> str:
        old = sys.stdout
        sys.stdout = buf = StringIO()
        try:
            self.printer.print_board(board)
        finally:
            sys.stdout = old
        return buf.getvalue()

    def test_empty_board(self) -> None:
        board = Board(2, 3)
        output = self._capture(board)
        self.assertEqual(output, ". . .\n. . .\n")

    def test_board_with_pieces(self) -> None:
        board = Board(2, 4)
        board.set_piece(Position(0, 0), Piece("w", "K"))
        board.set_piece(Position(1, 3), Piece("b", "K"))
        output = self._capture(board)
        self.assertEqual(output, "wK . . .\n. . . bK\n")

    def test_console_printer_alias(self) -> None:
        from kungfu_chess.io.board_printer import ConsoleBoardPrinter
        self.assertIs(ConsoleBoardPrinter, BoardPrinter)


if __name__ == "__main__":
    unittest.main()
