"""Unit tests for core.io.board_printer."""

import unittest
from io import StringIO

from core.model.board import ArrayBoard
from core.model.piece import TextPiece as Piece
from core.model.position import Position
from core.io.board_printer import BoardPrinter


class TestBoardPrinter(unittest.TestCase):

    def _capture(self, board: ArrayBoard) -> str:
        buf = StringIO()
        BoardPrinter(buf).print_board(board)
        return buf.getvalue()

    def test_empty_board(self) -> None:
        board = ArrayBoard(2, 3)
        output = self._capture(board)
        self.assertEqual(output, ". . .\n. . .\n")

    def test_board_with_pieces(self) -> None:
        board = ArrayBoard(2, 4)
        board.set_piece(Position(0, 0), Piece("w", "K"))
        board.set_piece(Position(1, 3), Piece("b", "K"))
        output = self._capture(board)
        self.assertEqual(output, "wK . . .\n. . . bK\n")


if __name__ == "__main__":
    unittest.main()
