"""Unit tests for kungfu_chess.realtime.real_time_arbiter."""

import unittest

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import ArrayBoard as Board
from kungfu_chess.model.piece import TextPiece as Piece
from kungfu_chess.model.game_state import GameState, Movement
from kungfu_chess.realtime.real_time_arbiter import (
    RealTimeArbiter,
    ChebyshevDistanceDuration,
    InstantMovementDuration,
    ProxyBoard,
)
from kungfu_chess.rules.rule_engine import PathChecker
from kungfu_chess.rules.piece_rules import StandardPawnPromotion
from kungfu_chess.config.game_config import GameConfig


def _make_arbiter(ms_per_square: int = 1000) -> RealTimeArbiter:
    config = GameConfig()
    return RealTimeArbiter(
        duration_strategy=ChebyshevDistanceDuration(ms_per_square=ms_per_square),
        path_checker=PathChecker(),
        config=config,
        promotion_strategy=StandardPawnPromotion(),
    )


class TestChebyshevDuration(unittest.TestCase):
    def test_duration_one_square(self) -> None:
        d = ChebyshevDistanceDuration(ms_per_square=500)
        p = Piece("w", "R")
        self.assertEqual(d.calculate_duration(Position(0, 0), Position(0, 1), p), 500)

    def test_duration_diagonal(self) -> None:
        d = ChebyshevDistanceDuration(ms_per_square=500)
        p = Piece("w", "B")
        # Chebyshev distance of (3, 3) diagonal = 3
        self.assertEqual(d.calculate_duration(Position(0, 0), Position(3, 3), p), 1500)

    def test_instant_duration(self) -> None:
        d = InstantMovementDuration()
        p = Piece("w", "K")
        self.assertEqual(d.calculate_duration(Position(0, 0), Position(7, 7), p), 0)


class TestGetPositionAt(unittest.TestCase):
    def setUp(self) -> None:
        self.arbiter = _make_arbiter(ms_per_square=1000)

    def _mov(self, frm, to, start_ms, arrival_ms) -> Movement:
        return Movement(frm=frm, to=to, piece=Piece("w", "R"),
                        start_ms=start_ms, arrival_ms=arrival_ms)

    def test_before_start_returns_frm(self) -> None:
        mov = self._mov(Position(0, 0), Position(0, 3), start_ms=1000, arrival_ms=4000)
        self.assertEqual(self.arbiter.get_position_at(mov, 500), Position(0, 0))

    def test_after_arrival_returns_to(self) -> None:
        mov = self._mov(Position(0, 0), Position(0, 3), start_ms=0, arrival_ms=3000)
        self.assertEqual(self.arbiter.get_position_at(mov, 5000), Position(0, 3))

    def test_mid_transit(self) -> None:
        mov = self._mov(Position(0, 0), Position(0, 3), start_ms=0, arrival_ms=3000)
        # At t=1000 (1/3 of the way): should be at col 1
        pos = self.arbiter.get_position_at(mov, 1000)
        self.assertEqual(pos, Position(0, 1))


class TestResolveMovements(unittest.TestCase):
    def setUp(self) -> None:
        self.arbiter = _make_arbiter(ms_per_square=1000)
        self.config = GameConfig()

    def test_piece_arrives_at_destination(self) -> None:
        board = Board(8, 8)
        rook = Piece("w", "R")
        board.set_piece(Position(0, 0), rook)

        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 3),
                       piece=rook, start_ms=0, arrival_ms=3000)
        state.active_movements.append(mov)
        rook.transition_to_moving()

        state.clock_ms = 3000
        self.arbiter.resolve_movements(board, state, 3000)

        self.assertIsNone(board.get_piece(Position(0, 0)))
        self.assertEqual(board.get_piece(Position(0, 3)), rook)
        self.assertEqual(len(state.active_movements), 0)

    def test_piece_not_arrived_before_time(self) -> None:
        board = Board(8, 8)
        rook = Piece("w", "R")
        board.set_piece(Position(0, 0), rook)

        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 3),
                       piece=rook, start_ms=0, arrival_ms=3000)
        state.active_movements.append(mov)
        rook.transition_to_moving()

        self.arbiter.resolve_movements(board, state, 1000)

        self.assertEqual(board.get_piece(Position(0, 0)), rook)
        self.assertIsNone(board.get_piece(Position(0, 3)))
        self.assertEqual(len(state.active_movements), 1)

    def test_capture_on_arrival(self) -> None:
        board = Board(8, 8)
        rook = Piece("w", "R")
        enemy = Piece("b", "P")
        board.set_piece(Position(0, 0), rook)
        board.set_piece(Position(0, 3), enemy)

        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 3),
                       piece=rook, start_ms=0, arrival_ms=3000)
        state.active_movements.append(mov)
        rook.transition_to_moving()

        state.clock_ms = 3000
        self.arbiter.resolve_movements(board, state, 3000)

        self.assertEqual(board.get_piece(Position(0, 3)), rook)

    def test_king_capture_sets_game_over(self) -> None:
        board = Board(8, 8)
        config = GameConfig()
        rook = Piece("w", "R")
        king = Piece("b", "K")
        board.set_piece(Position(0, 0), rook)
        board.set_piece(Position(0, 5), king)

        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 5),
                       piece=rook, start_ms=0, arrival_ms=5000)
        state.active_movements.append(mov)
        rook.transition_to_moving()

        state.clock_ms = 5000
        self.arbiter.resolve_movements(board, state, 5000)

        self.assertTrue(state.game_over)
        self.assertEqual(state.game_over_reason, "king_captured")


class TestProxyBoard(unittest.TestCase):
    def test_moving_piece_hidden_at_origin(self) -> None:
        board = Board(4, 4)
        rook = Piece("w", "R")
        board.set_piece(Position(0, 0), rook)

        arbiter = _make_arbiter(1000)
        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 3),
                       piece=rook, start_ms=0, arrival_ms=3000)
        state.active_movements.append(mov)

        proxy = arbiter.get_effective_board(board, state, 1000)
        # At t=1000, rook is at (0, 1) — origin should be empty
        self.assertIsNone(proxy.get_piece(Position(0, 0)))
        self.assertEqual(proxy.get_piece(Position(0, 1)), rook)


class TestArbiterCollisions(unittest.TestCase):
    def setUp(self) -> None:
        self.arbiter = _make_arbiter(ms_per_square=1000)
        self.board = Board(8, 8)
        self.state = GameState()

    def _add_movement(self, piece, frm, to, start, arrival):
        self.board.set_piece(frm, piece)
        mov = Movement(frm=frm, to=to, piece=piece, start_ms=start, arrival_ms=arrival)
        self.state.active_movements.append(mov)
        piece.transition_to_moving()
        return mov

    def test_same_square_collision(self) -> None:
        p1 = Piece("w", "R")
        p2 = Piece("b", "R")
        self._add_movement(p1, Position(0, 0), Position(0, 2), 0, 2000)
        self._add_movement(p2, Position(0, 4), Position(0, 2), 100, 2100)
        
        self.state.clock_ms = 2000
        self.arbiter.resolve_movements(self.board, self.state, 2000)
        self.state.clock_ms = 2100
        self.arbiter.resolve_movements(self.board, self.state, 2100)
        
        # p1 arrived earlier, so p1 lands on (0, 2). Then p2 arrives and captures p1!
        self.assertEqual(self.board.get_piece(Position(0, 2)), p2)
        self.assertEqual(len(self.state.active_movements), 0)

    def test_swap_path_collision(self) -> None:
        p1 = Piece("w", "R")
        p2 = Piece("b", "R")
        m1 = self._add_movement(p1, Position(0, 0), Position(0, 2), 0, 2000)
        m2 = self._add_movement(p2, Position(0, 2), Position(0, 0), 0, 2000)
        
        # They will cross paths at (0, 1)
        self.state.clock_ms = 1000
        self.arbiter.resolve_movements(self.board, self.state, 1000)
        
        # p1 should capture p2 due to index priority
        # If p1 captures p2, p1 keeps moving and p2 is removed from the board.
        self.assertEqual(len(self.state.active_movements), 1)
        pieces_on_board = sum(1 for r in range(self.board.rows) for c in range(self.board.cols) if self.board.get_piece(Position(r, c)) is not None)
        self.assertEqual(pieces_on_board, 1)

    def test_jump_collision(self) -> None:
        p1 = Piece("w", "N")
        p2 = Piece("b", "R")
        m1 = self._add_movement(p1, Position(0, 0), Position(1, 2), 0, 2000)
        p1.transition_to_idle()
        p1.transition_to_jumping()
        m2 = self._add_movement(p2, Position(0, 2), Position(1, 2), 0, 2000)
        
        self.state.clock_ms = 2000
        self.arbiter.resolve_movements(self.board, self.state, 2000)
        
        # Jumping piece (p1) wins same-square collision
        self.assertEqual(self.board.get_piece(Position(1, 2)), p1)

    def test_pawn_promotion(self) -> None:
        p = Piece("w", "P")
        self._add_movement(p, Position(1, 0), Position(0, 0), 0, 1000)
        
        self.state.clock_ms = 1000
        self.arbiter.resolve_movements(self.board, self.state, 1000)
        
        promoted = self.board.get_piece(Position(0, 0))
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.piece_type, "Q")
        
    def test_cooldown_expiration(self) -> None:
        p = Piece("w", "R")
        p.transition_to_cooldown()
        from kungfu_chess.model.game_state import Cooldown
        self.state.active_cooldowns.append(Cooldown(piece=p, end_ms=1000))
        
        self.state.clock_ms = 1500
        self.arbiter.resolve_movements(self.board, self.state, 1500)
        
        self.assertEqual(len(self.state.active_cooldowns), 0)
        self.assertTrue(p.can_move())

if __name__ == "__main__":
    unittest.main()

