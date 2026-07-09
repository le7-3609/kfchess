from kfchess.rules.move_validators import KingMoveValidator, QueenMoveValidator, RookMoveValidator, BishopMoveValidator, KnightMoveValidator, PawnMoveValidator
from kfchess.config.game_config import GameConfig
import unittest

from kfchess.models.board import Board, Position
from kfchess.models.piece import TextPiece as Piece, PieceFactory


class TestPiece(unittest.TestCase):
    def test_piece_creation_and_string(self) -> None:
        self.assertEqual(str(Piece("w", "K")), "wK")
        self.assertEqual(str(Piece("b", "P")), "bP")

    def test_piece_equality(self) -> None:
        p1 = Piece("w", "K")
        p2 = Piece("w", "K")
        p3 = Piece("b", "K")
        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, p3)
        self.assertNotEqual(p1, "wK")

    def test_from_string(self) -> None:
        p = PieceFactory.from_string("wK")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.color, "w")
        self.assertEqual(p.piece_type, "K")

        self.assertIsNone(PieceFactory.from_string("xK"))
        self.assertIsNone(PieceFactory.from_string("wX"))
        self.assertIsNone(PieceFactory.from_string("w"))
        self.assertIsNone(PieceFactory.from_string("wKK"))


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
        piece = Piece("b", "Q")
        board.set_piece(pos, piece)
        self.assertEqual(board.get_piece(pos), piece)

        with self.assertRaises(IndexError):
            board.get_piece(Position(5, 5))
        with self.assertRaises(IndexError):
            board.set_piece(Position(5, 5), piece)


if __name__ == '__main__':
    unittest.main()
