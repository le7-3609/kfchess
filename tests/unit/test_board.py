"""Unit tests for kungfu_chess.model.board."""

import unittest

from kungfu_chess.errors import EmptyCellError, InvalidPositionError, OccupiedCellError
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

    def test_add_piece_to_empty_cell(self) -> None:
        board = ArrayBoard(4, 4)
        pos = Position(1, 2)
        piece = Piece("b", "Q")
        board.add_piece(pos, piece)
        self.assertIs(board.get_piece(pos), piece)

    def test_add_piece_to_occupied_cell_fails(self) -> None:
        board = ArrayBoard(4, 4)
        pos = Position(1, 2)
        board.add_piece(pos, Piece("b", "Q"))
        with self.assertRaises(OccupiedCellError):
            board.add_piece(pos, Piece("w", "K"))

    def test_move_piece_updates_source_and_destination(self) -> None:
        board = ArrayBoard(4, 4)
        frm, to = Position(1, 1), Position(2, 2)
        piece = Piece("w", "N")
        board.add_piece(frm, piece)
        board.move_piece(frm, to)
        self.assertIsNone(board.get_piece(frm))
        self.assertIs(board.get_piece(to), piece)

    def test_move_piece_from_empty_cell_fails(self) -> None:
        board = ArrayBoard(4, 4)
        with self.assertRaises(EmptyCellError):
            board.move_piece(Position(0, 0), Position(1, 1))

    def test_remove_captured_piece_clears_its_cell(self) -> None:
        board = ArrayBoard(4, 4)
        pos = Position(3, 3)
        piece = Piece("b", "P")
        board.add_piece(pos, piece)
        removed = board.remove_piece(pos)
        self.assertIs(removed, piece)
        self.assertIsNone(board.get_piece(pos))

    def test_remove_piece_from_empty_cell_returns_none(self) -> None:
        board = ArrayBoard(4, 4)
        self.assertIsNone(board.remove_piece(Position(0, 0)))


if __name__ == "__main__":
    unittest.main()
