import unittest

from kfchess.models.board import Board, Position
from kfchess.models.piece import Color, Piece, PieceType


class TestColorAndPieceType(unittest.TestCase):
    def test_color_values(self) -> None:
        self.assertTrue(Color.has_value('w'))
        self.assertTrue(Color.has_value('b'))
        self.assertFalse(Color.has_value('x'))

    def test_piece_type_values(self) -> None:
        for val in ['K', 'Q', 'R', 'B', 'N', 'P']:
            self.assertTrue(PieceType.has_value(val))
        self.assertFalse(PieceType.has_value('X'))


class TestPiece(unittest.TestCase):
    def test_piece_creation_and_string(self) -> None:
        self.assertEqual(str(Piece(Color.WHITE, PieceType.KING)), "wK")
        self.assertEqual(str(Piece(Color.BLACK, PieceType.PAWN)), "bP")

    def test_piece_equality(self) -> None:
        p1 = Piece(Color.WHITE, PieceType.KING)
        p2 = Piece(Color.WHITE, PieceType.KING)
        p3 = Piece(Color.BLACK, PieceType.KING)
        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, p3)
        self.assertNotEqual(p1, "wK")

    def test_from_string(self) -> None:
        p = Piece.from_string("wK")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.color, Color.WHITE)
        self.assertEqual(p.piece_type, PieceType.KING)

        self.assertIsNone(Piece.from_string("xK"))
        self.assertIsNone(Piece.from_string("wX"))
        self.assertIsNone(Piece.from_string("w"))
        self.assertIsNone(Piece.from_string("wKK"))


class TestBoard(unittest.TestCase):
    def test_board_size_and_empty(self) -> None:
        board = Board(3, 4)
        self.assertEqual(board.rows, 3)
        self.assertEqual(board.cols, 4)
        for r in range(3):
            for c in range(4):
                self.assertIsNone(board.get_piece(Position(r, c)))

    def test_board_bounds(self) -> None:
        board = Board(3, 3)
        self.assertTrue(board.is_valid_position(Position(0, 0)))
        self.assertTrue(board.is_valid_position(Position(2, 2)))
        self.assertFalse(board.is_valid_position(Position(-1, 0)))
        self.assertFalse(board.is_valid_position(Position(0, 3)))

    def test_set_and_get_piece(self) -> None:
        board = Board(4, 4)
        pos = Position(1, 2)
        piece = Piece(Color.BLACK, PieceType.QUEEN)
        board.set_piece(pos, piece)
        self.assertEqual(board.get_piece(pos), piece)

        with self.assertRaises(IndexError):
            board.get_piece(Position(5, 5))
        with self.assertRaises(IndexError):
            board.set_piece(Position(5, 5), piece)

    def test_get_row_tokens(self) -> None:
        board = Board(2, 3)
        board.set_piece(Position(0, 1), Piece(Color.WHITE, PieceType.PAWN))
        board.set_piece(Position(1, 2), Piece(Color.BLACK, PieceType.ROOK))

        self.assertEqual(board.get_row_tokens(0), ['.', 'wP', '.'])
        self.assertEqual(board.get_row_tokens(1), ['.', '.', 'bR'])

        with self.assertRaises(IndexError):
            board.get_row_tokens(2)


if __name__ == '__main__':
    unittest.main()
