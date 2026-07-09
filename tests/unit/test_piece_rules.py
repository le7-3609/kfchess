"""Unit tests for kungfu_chess.rules.piece_rules — validators and factory."""

import unittest

from kungfu_chess.model.position import Position
from kungfu_chess.rules.piece_rules import (
    KingMoveValidator,
    QueenMoveValidator,
    RookMoveValidator,
    BishopMoveValidator,
    KnightMoveValidator,
    PawnMoveValidator,
    MoveValidatorFactory,
    StandardPawnPromotion,
)
from kungfu_chess.config.game_config import GameConfig
from kungfu_chess.model.piece import TextPiece as Piece


class TestKingMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = KingMoveValidator()

    def test_one_square_in_any_direction(self) -> None:
        frm = Position(4, 4)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    self.assertFalse(self.v.is_legal(frm, Position(4, 4)))
                else:
                    self.assertTrue(self.v.is_legal(frm, Position(4 + dr, 4 + dc)))

    def test_two_squares_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(Position(4, 4), Position(4, 6)))
        self.assertFalse(self.v.is_legal(Position(4, 4), Position(2, 4)))


class TestRookMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = RookMoveValidator()

    def test_horizontal_move(self) -> None:
        self.assertTrue(self.v.is_legal(Position(3, 0), Position(3, 7)))

    def test_vertical_move(self) -> None:
        self.assertTrue(self.v.is_legal(Position(0, 5), Position(7, 5)))

    def test_diagonal_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(Position(0, 0), Position(3, 3)))

    def test_no_move_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(Position(3, 3), Position(3, 3)))


class TestBishopMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = BishopMoveValidator()

    def test_diagonal_move(self) -> None:
        self.assertTrue(self.v.is_legal(Position(0, 0), Position(4, 4)))
        self.assertTrue(self.v.is_legal(Position(4, 4), Position(1, 1)))

    def test_horizontal_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(Position(3, 3), Position(3, 6)))

    def test_no_move_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(Position(3, 3), Position(3, 3)))


class TestQueenMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = QueenMoveValidator()

    def test_straight_move(self) -> None:
        self.assertTrue(self.v.is_legal(Position(3, 3), Position(3, 7)))

    def test_diagonal_move(self) -> None:
        self.assertTrue(self.v.is_legal(Position(3, 3), Position(6, 6)))

    def test_l_shape_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(Position(0, 0), Position(1, 2)))


class TestKnightMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = KnightMoveValidator()

    def test_valid_l_shapes(self) -> None:
        frm = Position(4, 4)
        valid_targets = [
            Position(2, 3), Position(2, 5),
            Position(6, 3), Position(6, 5),
            Position(3, 2), Position(3, 6),
            Position(5, 2), Position(5, 6),
        ]
        for target in valid_targets:
            self.assertTrue(self.v.is_legal(frm, target), f"Expected legal: {frm} -> {target}")

    def test_straight_move_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(Position(4, 4), Position(4, 6)))


class TestPawnMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.config = GameConfig()
        self.v = PawnMoveValidator(self.config)

    def test_white_forward_one(self) -> None:
        # white moves up (decreasing row)
        self.assertTrue(self.v.is_legal(Position(5, 3), Position(4, 3), color="w"))

    def test_white_forward_two_from_start(self) -> None:
        self.assertTrue(self.v.is_legal(Position(6, 3), Position(4, 3), color="w"))

    def test_white_forward_two_not_from_start(self) -> None:
        self.assertFalse(self.v.is_legal(Position(4, 3), Position(2, 3), color="w"))

    def test_white_diagonal_capture(self) -> None:
        self.assertTrue(self.v.is_legal(Position(5, 3), Position(4, 4), color="w"))

    def test_black_forward_one(self) -> None:
        self.assertTrue(self.v.is_legal(Position(2, 3), Position(3, 3), color="b"))

    def test_white_backward_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(Position(5, 3), Position(6, 3), color="w"))


class TestMoveValidatorFactory(unittest.TestCase):
    def test_get_validator(self) -> None:
        cfg = GameConfig()
        factory = MoveValidatorFactory({
            "K": KingMoveValidator(),
            "Q": QueenMoveValidator(),
            "R": RookMoveValidator(),
            "B": BishopMoveValidator(),
            "N": KnightMoveValidator(),
            "P": PawnMoveValidator(cfg),
        })
        for piece_type in ("K", "Q", "R", "B", "N", "P"):
            v = factory.get_validator(piece_type)
            self.assertIsNotNone(v)

    def test_unknown_type_raises(self) -> None:
        factory = MoveValidatorFactory({"K": KingMoveValidator()})
        with self.assertRaises(KeyError):
            factory.get_validator("X")


class TestStandardPawnPromotion(unittest.TestCase):
    def test_promote_white_pawn_at_rank_0(self) -> None:
        config = GameConfig()
        promo = StandardPawnPromotion()
        piece = Piece("w", "P")
        promo.evaluate_promotion(piece, Position(0, 4), config)
        self.assertEqual(piece.piece_type, "Q")

    def test_no_promotion_in_middle(self) -> None:
        config = GameConfig()
        promo = StandardPawnPromotion()
        piece = Piece("w", "P")
        promo.evaluate_promotion(piece, Position(3, 4), config)
        self.assertEqual(piece.piece_type, "P")

    def test_promote_black_pawn_at_last_rank(self) -> None:
        config = GameConfig()
        promo = StandardPawnPromotion()
        piece = Piece("b", "P")
        promo.evaluate_promotion(piece, Position(config.board_rows - 1, 2), config)
        self.assertEqual(piece.piece_type, "Q")


if __name__ == "__main__":
    unittest.main()
