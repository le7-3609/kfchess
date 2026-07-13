"""Unit tests for kungfu_chess.model.board."""

import unittest

from kungfu_chess.errors import InvalidPositionError
from kungfu_chess.model.position import Position
from kungfu_chess.model.board import ArrayBoard
from kungfu_chess.model.piece import TextPiece as Piece, PieceFactory


class TestArrayBoard(unittest.TestCase):

    def test_board_dimensions(self) -> None:
        board = ArrayBoard(3, 4)
        self.assertEqual(board.rows, 3)
        self.assertEqual(board.cols, 4)

    def test_initial_board_is_empty(self) -> None:
        board = ArrayBoard(4, 4)
        for r in range(4):
            for c in range(4):
                self.assertIsNone(board.get_piece(Position(r, c)))

    def test_is_valid_position(self) -> None:
        board = ArrayBoard(3, 3)
        self.assertTrue(board.is_valid_position(Position(0, 0)))
        self.assertTrue(board.is_valid_position(Position(2, 2)))
        self.assertFalse(board.is_valid_position(Position(-1, 0)))
        self.assertFalse(board.is_valid_position(Position(0, 3)))
        self.assertFalse(board.is_valid_position(Position(3, 0)))

    def test_set_and_get_piece(self) -> None:
        board = ArrayBoard(4, 4)
        pos = Position(1, 2)
        piece = Piece("b", "Q")
        board.set_piece(pos, piece)
        self.assertIs(board.get_piece(pos), piece)

    def test_clear_piece(self) -> None:
        board = ArrayBoard(2, 2)
        pos = Position(0, 0)
        board.set_piece(pos, Piece("w", "K"))
        board.set_piece(pos, None)
        self.assertIsNone(board.get_piece(pos))

    def test_out_of_bounds_raises(self) -> None:
        board = ArrayBoard(3, 3)
        with self.assertRaises(InvalidPositionError):
            board.get_piece(Position(5, 5))
        with self.assertRaises(InvalidPositionError):
            board.set_piece(Position(5, 5), Piece("w", "K"))


if __name__ == "__main__":
    unittest.main()
