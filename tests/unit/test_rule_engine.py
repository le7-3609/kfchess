"""Unit tests for shared.rules.rule_engine — RuleEngine, PathChecker, ThreatValidator."""

import unittest

from shared.model.position import Position
from shared.model.board import ArrayBoard
from shared.model.piece import TextPiece as Piece
from shared.rules.rule_engine import MoveValidation, PathChecker, RuleEngine, ThreatValidator
from shared.rules.piece_rules import (
    MoveValidatorFactory,
    KingMoveValidator,
    QueenMoveValidator,
    RookMoveValidator,
    BishopMoveValidator,
    KnightMoveValidator,
    PawnMoveValidator,
)
from shared.config.game_config import GameConfig


def _make_factory() -> MoveValidatorFactory:
    cfg = GameConfig()
    return MoveValidatorFactory({
        "K": KingMoveValidator(),
        "Q": QueenMoveValidator(),
        "R": RookMoveValidator(),
        "B": BishopMoveValidator(),
        "N": KnightMoveValidator(),
        "P": PawnMoveValidator(cfg),
    })


class TestRuleEngine(unittest.TestCase):

    def setUp(self) -> None:
        self.engine = RuleEngine(move_validator_factory=_make_factory())

    def _board(self, rows: int = 8, cols: int = 8) -> ArrayBoard:
        return ArrayBoard(rows, cols)

    def test_valid_move_returns_ok(self) -> None:
        board = self._board()
        board.set_piece(Position(3, 0), Piece("w", "R"))
        result = self.engine.validate_move(board, Position(3, 0), Position(3, 7))
        self.assertEqual(result, MoveValidation(True, "ok"))

    def test_source_outside_board(self) -> None:
        board = self._board()
        result = self.engine.validate_move(board, Position(-1, 0), Position(3, 7))
        self.assertEqual(result, MoveValidation(False, "outside_board"))

    def test_destination_outside_board(self) -> None:
        board = self._board()
        board.set_piece(Position(3, 0), Piece("w", "R"))
        result = self.engine.validate_move(board, Position(3, 0), Position(8, 0))
        self.assertEqual(result, MoveValidation(False, "outside_board"))

    def test_empty_source(self) -> None:
        board = self._board()
        result = self.engine.validate_move(board, Position(3, 0), Position(3, 7))
        self.assertEqual(result, MoveValidation(False, "empty_source"))

    def test_friendly_destination(self) -> None:
        board = self._board()
        board.set_piece(Position(3, 0), Piece("w", "R"))
        board.set_piece(Position(3, 7), Piece("w", "B"))
        result = self.engine.validate_move(board, Position(3, 0), Position(3, 7))
        self.assertEqual(result, MoveValidation(False, "friendly_destination"))

    def test_illegal_piece_move(self) -> None:
        board = self._board()
        board.set_piece(Position(3, 0), Piece("w", "R"))
        result = self.engine.validate_move(board, Position(3, 0), Position(4, 1))
        self.assertEqual(result, MoveValidation(False, "illegal_piece_move"))

    def test_capture_of_enemy_is_valid(self) -> None:
        board = self._board()
        board.set_piece(Position(3, 0), Piece("w", "R"))
        board.set_piece(Position(3, 7), Piece("b", "B"))
        result = self.engine.validate_move(board, Position(3, 0), Position(3, 7))
        self.assertEqual(result, MoveValidation(True, "ok"))

    def test_detects_blocked_path(self) -> None:
        """legal_destinations is blocking-aware, so RuleEngine's common route is too."""
        board = self._board()
        board.set_piece(Position(3, 0), Piece("w", "R"))
        board.set_piece(Position(3, 4), Piece("b", "P"))
        result = self.engine.validate_move(board, Position(3, 0), Position(3, 7))
        self.assertEqual(result, MoveValidation(False, "illegal_piece_move"))


class TestPathChecker(unittest.TestCase):

    def setUp(self) -> None:
        self.pc = PathChecker(_make_factory(), GameConfig())

    def _board(self, rows: int = 8, cols: int = 8) -> ArrayBoard:
        return ArrayBoard(rows, cols)

    # ------------------------------------------------------------------
    # is_path_clear
    # ------------------------------------------------------------------

    def test_rook_clear_path(self) -> None:
        board = self._board()
        board.set_piece(Position(3, 0), Piece("w", "R"))
        self.assertTrue(self.pc.is_path_clear(board, Position(3, 0), Position(3, 7)))

    def test_rook_blocked_path(self) -> None:
        board = self._board()
        board.set_piece(Position(3, 0), Piece("w", "R"))
        board.set_piece(Position(3, 4), Piece("b", "P"))
        self.assertFalse(self.pc.is_path_clear(board, Position(3, 0), Position(3, 7)))

    def test_bishop_clear_diagonal(self) -> None:
        board = self._board()
        board.set_piece(Position(0, 0), Piece("w", "B"))
        self.assertTrue(self.pc.is_path_clear(board, Position(0, 0), Position(4, 4)))

    def test_bishop_blocked_diagonal(self) -> None:
        board = self._board()
        board.set_piece(Position(0, 0), Piece("w", "B"))
        board.set_piece(Position(2, 2), Piece("b", "N"))
        self.assertFalse(self.pc.is_path_clear(board, Position(0, 0), Position(4, 4)))

    def test_knight_never_blocked(self) -> None:
        board = self._board()
        board.set_piece(Position(4, 4), Piece("w", "N"))
        # Fill every square between start and a possible L-target
        board.set_piece(Position(4, 5), Piece("b", "P"))
        board.set_piece(Position(5, 4), Piece("b", "P"))
        self.assertTrue(self.pc.is_path_clear(board, Position(4, 4), Position(2, 5)))

    def test_king_never_blocked(self) -> None:
        board = self._board()
        board.set_piece(Position(4, 4), Piece("w", "K"))
        board.set_piece(Position(4, 5), Piece("b", "P"))
        self.assertTrue(self.pc.is_path_clear(board, Position(4, 4), Position(4, 5)))

    # ------------------------------------------------------------------
    # can_land
    # ------------------------------------------------------------------

    def test_can_land_on_empty(self) -> None:
        board = self._board()
        piece = Piece("w", "R")
        board.set_piece(Position(0, 0), piece)
        self.assertTrue(self.pc.can_land(board, piece, Position(0, 0), Position(0, 5)))

    def test_cannot_land_on_friendly(self) -> None:
        board = self._board()
        piece = Piece("w", "R")
        board.set_piece(Position(0, 0), piece)
        board.set_piece(Position(0, 5), Piece("w", "B"))
        self.assertFalse(self.pc.can_land(board, piece, Position(0, 0), Position(0, 5)))

    def test_can_land_on_enemy(self) -> None:
        board = self._board()
        piece = Piece("w", "R")
        board.set_piece(Position(0, 0), piece)
        board.set_piece(Position(0, 5), Piece("b", "R"))
        self.assertTrue(self.pc.can_land(board, piece, Position(0, 0), Position(0, 5)))

    def test_pawn_cannot_capture_forward(self) -> None:
        board = self._board()
        pawn = Piece("w", "P")
        board.set_piece(Position(5, 3), pawn)
        board.set_piece(Position(4, 3), Piece("b", "P"))
        self.assertFalse(self.pc.can_land(board, pawn, Position(5, 3), Position(4, 3)))

    def test_pawn_can_capture_diagonally(self) -> None:
        board = self._board()
        pawn = Piece("w", "P")
        board.set_piece(Position(5, 3), pawn)
        board.set_piece(Position(4, 4), Piece("b", "P"))
        self.assertTrue(self.pc.can_land(board, pawn, Position(5, 3), Position(4, 4)))

    def test_pawn_cannot_diagonal_empty(self) -> None:
        board = self._board()
        pawn = Piece("w", "P")
        board.set_piece(Position(5, 3), pawn)
        self.assertFalse(self.pc.can_land(board, pawn, Position(5, 3), Position(4, 4)))

    def test_pawn_en_passant(self) -> None:
        board = self._board()
        pawn = Piece("w", "P")
        board.set_piece(Position(3, 3), pawn)
        ep_pos = Position(2, 4)
        self.assertTrue(self.pc.can_land(board, pawn, Position(3, 3), ep_pos, en_passant_targets=[ep_pos]))


class TestThreatValidator(unittest.TestCase):

    def _setup(self, board: ArrayBoard) -> ThreatValidator:
        config = GameConfig()
        factory = _make_factory()
        return ThreatValidator(move_validator_factory=factory, path_checker=PathChecker(factory, config), config=config)

    def test_king_not_threatened(self) -> None:
        board = ArrayBoard(8, 8)
        board.set_piece(Position(0, 0), Piece("w", "K"))
        board.set_piece(Position(7, 7), Piece("b", "K"))
        tv = self._setup(board)
        self.assertFalse(tv.is_king_threatened(board, "w"))

    def test_king_threatened_by_rook(self) -> None:
        board = ArrayBoard(8, 8)
        board.set_piece(Position(0, 0), Piece("w", "K"))
        board.set_piece(Position(0, 5), Piece("b", "R"))
        board.set_piece(Position(7, 7), Piece("b", "K"))
        tv = self._setup(board)
        self.assertTrue(tv.is_king_threatened(board, "w"))

    def test_king_not_threatened_when_blocked(self) -> None:
        board = ArrayBoard(8, 8)
        board.set_piece(Position(0, 0), Piece("w", "K"))
        board.set_piece(Position(0, 3), Piece("w", "P"))  # blocker
        board.set_piece(Position(0, 5), Piece("b", "R"))
        board.set_piece(Position(7, 7), Piece("b", "K"))
        tv = self._setup(board)
        self.assertFalse(tv.is_king_threatened(board, "w"))

    def test_no_king_returns_false(self) -> None:
        board = ArrayBoard(4, 4)
        board.set_piece(Position(0, 0), Piece("b", "R"))
        tv = self._setup(board)
        self.assertFalse(tv.is_king_threatened(board, "w"))

    def test_pinned_piece_cannot_move_off_pin_line(self) -> None:
        # wK(0,0) . . . wR(0,4) . . bR(0,7) -- wR is pinned along rank 0.
        board = ArrayBoard(8, 8)
        board.set_piece(Position(0, 0), Piece("w", "K"))
        wr = Piece("w", "R")
        board.set_piece(Position(0, 4), wr)
        board.set_piece(Position(0, 7), Piece("b", "R"))
        tv = self._setup(board)
        # Moving off the rank would uncover the check on wK.
        self.assertFalse(tv.is_move_safe_from_check(board, Position(0, 4), Position(1, 4), wr))
        # Board must be restored (unmutated) after the simulation.
        self.assertIs(board.get_piece(Position(0, 4)), wr)
        self.assertIsNone(board.get_piece(Position(1, 4)))

    def test_pinned_piece_can_move_along_pin_line(self) -> None:
        board = ArrayBoard(8, 8)
        board.set_piece(Position(0, 0), Piece("w", "K"))
        wr = Piece("w", "R")
        board.set_piece(Position(0, 4), wr)
        board.set_piece(Position(0, 7), Piece("b", "R"))
        tv = self._setup(board)
        # Sliding along the same rank keeps the king shielded.
        self.assertTrue(tv.is_move_safe_from_check(board, Position(0, 4), Position(0, 5), wr))

    def test_unpinned_piece_move_is_safe(self) -> None:
        board = ArrayBoard(8, 8)
        board.set_piece(Position(0, 0), Piece("w", "K"))
        board.set_piece(Position(7, 7), Piece("b", "K"))
        wn = Piece("w", "N")
        board.set_piece(Position(4, 4), wn)
        tv = self._setup(board)
        self.assertTrue(tv.is_move_safe_from_check(board, Position(4, 4), Position(6, 5), wn))


if __name__ == "__main__":
    unittest.main()
