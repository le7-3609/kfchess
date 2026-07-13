"""Unit tests for kungfu_chess.rules.piece_rules — validators and factory."""

import unittest

from kungfu_chess.errors import MissingValidatorError
from kungfu_chess.model.position import Position
from kungfu_chess.model.board import ArrayBoard
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


def _board(rows: int = 8, cols: int = 8) -> ArrayBoard:
    return ArrayBoard(rows, cols)


class TestRookMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = RookMoveValidator()

    def test_moves_across_empty_row_and_column(self) -> None:
        board = _board()
        rook = Piece("w", "R")
        board.add_piece(Position(3, 3), rook)
        destinations = self.v.legal_destinations(board, rook)
        expected = {Position(3, c) for c in range(8) if c != 3}
        expected |= {Position(r, 3) for r in range(8) if r != 3}
        self.assertEqual(destinations, expected)

    def test_stops_before_friendly_blocker(self) -> None:
        board = _board()
        rook = Piece("w", "R")
        board.add_piece(Position(3, 3), rook)
        board.add_piece(Position(3, 6), Piece("w", "P"))
        destinations = self.v.legal_destinations(board, rook)
        self.assertIn(Position(3, 5), destinations)
        self.assertNotIn(Position(3, 6), destinations)
        self.assertNotIn(Position(3, 7), destinations)

    def test_captures_enemy_blocker_but_does_not_pass_it(self) -> None:
        board = _board()
        rook = Piece("w", "R")
        board.add_piece(Position(3, 3), rook)
        board.add_piece(Position(3, 6), Piece("b", "P"))
        destinations = self.v.legal_destinations(board, rook)
        self.assertIn(Position(3, 5), destinations)
        self.assertIn(Position(3, 6), destinations)
        self.assertNotIn(Position(3, 7), destinations)


class TestBishopMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = BishopMoveValidator()

    def test_moves_diagonally_not_straight(self) -> None:
        board = _board()
        bishop = Piece("w", "B")
        board.add_piece(Position(4, 4), bishop)
        destinations = self.v.legal_destinations(board, bishop)
        for pos in destinations:
            self.assertEqual(abs(pos.row - 4), abs(pos.col - 4))
        self.assertIn(Position(0, 0), destinations)
        self.assertIn(Position(7, 7), destinations)
        self.assertNotIn(Position(4, 0), destinations)

    def test_stops_before_friendly_blocker(self) -> None:
        board = _board()
        bishop = Piece("w", "B")
        board.add_piece(Position(4, 4), bishop)
        board.add_piece(Position(6, 6), Piece("w", "N"))
        destinations = self.v.legal_destinations(board, bishop)
        self.assertIn(Position(5, 5), destinations)
        self.assertNotIn(Position(6, 6), destinations)
        self.assertNotIn(Position(7, 7), destinations)

    def test_captures_enemy_blocker_but_does_not_pass_it(self) -> None:
        board = _board()
        bishop = Piece("w", "B")
        board.add_piece(Position(4, 4), bishop)
        board.add_piece(Position(6, 6), Piece("b", "N"))
        destinations = self.v.legal_destinations(board, bishop)
        self.assertIn(Position(5, 5), destinations)
        self.assertIn(Position(6, 6), destinations)
        self.assertNotIn(Position(7, 7), destinations)


class TestQueenMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = QueenMoveValidator()

    def test_combines_rook_and_bishop_movement(self) -> None:
        board = _board()
        queen = Piece("w", "Q")
        board.add_piece(Position(3, 3), queen)
        destinations = self.v.legal_destinations(board, queen)
        self.assertIn(Position(3, 7), destinations)  # rook-like
        self.assertIn(Position(0, 0), destinations)  # bishop-like
        self.assertNotIn(Position(4, 6), destinations)  # neither straight nor diagonal

    def test_stops_before_friendly_blocker(self) -> None:
        board = _board()
        queen = Piece("w", "Q")
        board.add_piece(Position(3, 3), queen)
        board.add_piece(Position(3, 6), Piece("w", "P"))
        destinations = self.v.legal_destinations(board, queen)
        self.assertIn(Position(3, 5), destinations)
        self.assertNotIn(Position(3, 6), destinations)
        self.assertNotIn(Position(3, 7), destinations)

    def test_captures_enemy_blocker_but_does_not_pass_it(self) -> None:
        board = _board()
        queen = Piece("w", "Q")
        board.add_piece(Position(3, 3), queen)
        board.add_piece(Position(0, 0), Piece("b", "N"))
        destinations = self.v.legal_destinations(board, queen)
        self.assertIn(Position(1, 1), destinations)
        self.assertIn(Position(0, 0), destinations)


class TestKnightMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = KnightMoveValidator()

    def test_jumps_over_blockers(self) -> None:
        board = _board()
        knight = Piece("w", "N")
        board.add_piece(Position(4, 4), knight)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                board.add_piece(Position(4 + dr, 4 + dc), Piece("w", "P"))
        destinations = self.v.legal_destinations(board, knight)
        expected = {
            Position(2, 3), Position(2, 5), Position(6, 3), Position(6, 5),
            Position(3, 2), Position(3, 6), Position(5, 2), Position(5, 6),
        }
        self.assertEqual(destinations, expected)

    def test_excludes_friendly_includes_enemy(self) -> None:
        board = _board()
        knight = Piece("w", "N")
        board.add_piece(Position(4, 4), knight)
        board.add_piece(Position(2, 3), Piece("w", "P"))
        board.add_piece(Position(2, 5), Piece("b", "P"))
        destinations = self.v.legal_destinations(board, knight)
        self.assertNotIn(Position(2, 3), destinations)
        self.assertIn(Position(2, 5), destinations)


class TestKingMoveValidator(unittest.TestCase):
    """Castling is a separate, stateful special move — see test_castling_validator.py."""

    def setUp(self) -> None:
        self.v = KingMoveValidator()

    def test_moves_one_cell_only(self) -> None:
        board = _board()
        king = Piece("w", "K")
        board.add_piece(Position(4, 4), king)
        destinations = self.v.legal_destinations(board, king)
        self.assertEqual(len(destinations), 8)
        for pos in destinations:
            self.assertLessEqual(max(abs(pos.row - 4), abs(pos.col - 4)), 1)

    def test_excludes_friendly_includes_enemy(self) -> None:
        board = _board()
        king = Piece("w", "K")
        board.add_piece(Position(4, 4), king)
        board.add_piece(Position(4, 5), Piece("w", "P"))
        board.add_piece(Position(5, 5), Piece("b", "P"))
        destinations = self.v.legal_destinations(board, king)
        self.assertNotIn(Position(4, 5), destinations)
        self.assertIn(Position(5, 5), destinations)


class TestPawnMoveValidator(unittest.TestCase):
    """En passant is a separate, stateful special move — see PathChecker.can_land."""

    def setUp(self) -> None:
        self.config = GameConfig()
        self.v = PawnMoveValidator(self.config)

    def test_white_forward_and_double_step_from_start(self) -> None:
        board = _board()
        pawn = Piece("w", "P")
        board.add_piece(Position(6, 3), pawn)
        destinations = self.v.legal_destinations(board, pawn)
        self.assertEqual(destinations, {Position(5, 3), Position(4, 3)})

    def test_double_step_blocked_if_single_step_occupied(self) -> None:
        board = _board()
        pawn = Piece("w", "P")
        board.add_piece(Position(6, 3), pawn)
        board.add_piece(Position(5, 3), Piece("b", "N"))
        destinations = self.v.legal_destinations(board, pawn)
        self.assertNotIn(Position(5, 3), destinations)
        self.assertNotIn(Position(4, 3), destinations)

    def test_forward_move_blocked_by_any_piece(self) -> None:
        board = _board()
        pawn = Piece("w", "P")
        board.add_piece(Position(5, 3), pawn)
        board.add_piece(Position(4, 3), Piece("b", "N"))
        destinations = self.v.legal_destinations(board, pawn)
        self.assertNotIn(Position(4, 3), destinations)

    def test_diagonal_capture_only_when_enemy_present(self) -> None:
        board = _board()
        pawn = Piece("w", "P")
        board.add_piece(Position(5, 3), pawn)
        board.add_piece(Position(4, 4), Piece("b", "N"))
        destinations = self.v.legal_destinations(board, pawn)
        self.assertIn(Position(4, 4), destinations)
        self.assertNotIn(Position(4, 2), destinations)

    def test_diagonal_blocked_by_friendly(self) -> None:
        board = _board()
        pawn = Piece("w", "P")
        board.add_piece(Position(5, 3), pawn)
        board.add_piece(Position(4, 4), Piece("w", "N"))
        destinations = self.v.legal_destinations(board, pawn)
        self.assertNotIn(Position(4, 4), destinations)

    def test_black_forward_direction(self) -> None:
        board = _board()
        pawn = Piece("b", "P")
        board.add_piece(Position(1, 3), pawn)
        destinations = self.v.legal_destinations(board, pawn)
        self.assertEqual(destinations, {Position(2, 3), Position(3, 3)})

    def test_en_passant_square_not_included(self) -> None:
        board = _board()
        pawn = Piece("w", "P")
        board.add_piece(Position(3, 3), pawn)
        board.add_piece(Position(3, 4), Piece("b", "P"))
        destinations = self.v.legal_destinations(board, pawn)
        self.assertNotIn(Position(2, 4), destinations)


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
        with self.assertRaises(MissingValidatorError):
            factory.get_validator("X")


class TestStandardPawnPromotion(unittest.TestCase):
    def test_promote_white_pawn_at_rank_0(self) -> None:
        config = GameConfig()
        promo = StandardPawnPromotion()
        piece = Piece("w", "P")
        promoted = promo.evaluate_promotion(piece, Position(0, 4), config)
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.piece_type, "Q")
        self.assertEqual(promoted.color, "w")
        # The original piece is untouched — promotion never mutates in place.
        self.assertEqual(piece.piece_type, "P")
        self.assertIsNot(promoted, piece)

    def test_no_promotion_in_middle(self) -> None:
        config = GameConfig()
        promo = StandardPawnPromotion()
        piece = Piece("w", "P")
        promoted = promo.evaluate_promotion(piece, Position(3, 4), config)
        self.assertIsNone(promoted)
        self.assertEqual(piece.piece_type, "P")

    def test_promote_black_pawn_at_last_rank(self) -> None:
        config = GameConfig()
        promo = StandardPawnPromotion()
        piece = Piece("b", "P")
        promoted = promo.evaluate_promotion(piece, Position(config.board_rows - 1, 2), config)
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.piece_type, "Q")
        self.assertEqual(promoted.color, "b")


if __name__ == "__main__":
    unittest.main()
