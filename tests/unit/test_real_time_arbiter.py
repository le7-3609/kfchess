"""Unit tests for kungfu_chess.realtime.real_time_arbiter."""

import unittest

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import ArrayBoard
from kungfu_chess.model.piece import TextPiece as Piece
from kungfu_chess.model.game_state import GameState, Movement
from kungfu_chess.realtime.real_time_arbiter import (
    RealTimeArbiter,
    ChebyshevDistanceDuration,
    InstantMovementDuration,
    ProxyBoard,
)
from kungfu_chess.rules.rule_engine import PathChecker
from kungfu_chess.rules.piece_rules import (
    StandardPawnPromotion,
    MoveValidatorFactory,
    KingMoveValidator,
    QueenMoveValidator,
    RookMoveValidator,
    BishopMoveValidator,
    KnightMoveValidator,
    PawnMoveValidator,
)
from kungfu_chess.config.game_config import GameConfig


def _make_factory(config: GameConfig) -> MoveValidatorFactory:
    return MoveValidatorFactory({
        "K": KingMoveValidator(),
        "Q": QueenMoveValidator(),
        "R": RookMoveValidator(),
        "B": BishopMoveValidator(),
        "N": KnightMoveValidator(),
        "P": PawnMoveValidator(config),
    })


def _make_arbiter(ms_per_square: int = 1000) -> RealTimeArbiter:
    config = GameConfig()
    return RealTimeArbiter(
        duration_strategy=ChebyshevDistanceDuration(ms_per_square=ms_per_square),
        path_checker=PathChecker(_make_factory(config), config),
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
        board = ArrayBoard(8, 8)
        rook = Piece("w", "R")
        board.set_piece(Position(0, 0), rook)

        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 3),
                       piece=rook, start_ms=0, arrival_ms=3000)
        self.arbiter.register_motion(mov)
        rook.transition_to_moving()

        state.clock_ms = 3000
        self.arbiter.resolve_movements(board, state, 3000)

        self.assertIsNone(board.get_piece(Position(0, 0)))
        self.assertEqual(board.get_piece(Position(0, 3)), rook)
        self.assertEqual(len(self.arbiter.movements()), 0)

    def test_piece_not_arrived_before_time(self) -> None:
        board = ArrayBoard(8, 8)
        rook = Piece("w", "R")
        board.set_piece(Position(0, 0), rook)

        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 3),
                       piece=rook, start_ms=0, arrival_ms=3000)
        self.arbiter.register_motion(mov)
        rook.transition_to_moving()

        self.arbiter.resolve_movements(board, state, 1000)

        self.assertEqual(board.get_piece(Position(0, 0)), rook)
        self.assertIsNone(board.get_piece(Position(0, 3)))
        self.assertEqual(len(self.arbiter.movements()), 1)

    def test_capture_on_arrival(self) -> None:
        board = ArrayBoard(8, 8)
        rook = Piece("w", "R")
        enemy = Piece("b", "P")
        board.set_piece(Position(0, 0), rook)
        board.set_piece(Position(0, 3), enemy)

        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 3),
                       piece=rook, start_ms=0, arrival_ms=3000)
        self.arbiter.register_motion(mov)
        rook.transition_to_moving()

        state.clock_ms = 3000
        self.arbiter.resolve_movements(board, state, 3000)

        self.assertEqual(board.get_piece(Position(0, 3)), rook)

    def test_king_capture_sets_game_over(self) -> None:
        board = ArrayBoard(8, 8)
        config = GameConfig()
        rook = Piece("w", "R")
        king = Piece("b", "K")
        board.set_piece(Position(0, 0), rook)
        board.set_piece(Position(0, 5), king)

        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 5),
                       piece=rook, start_ms=0, arrival_ms=5000)
        self.arbiter.register_motion(mov)
        rook.transition_to_moving()

        state.clock_ms = 5000
        self.arbiter.resolve_movements(board, state, 5000)

        self.assertTrue(state.game_over)
        self.assertEqual(state.game_over_reason, "king_captured")


class TestProxyBoard(unittest.TestCase):
    def test_moving_piece_hidden_at_origin(self) -> None:
        board = ArrayBoard(4, 4)
        rook = Piece("w", "R")
        board.set_piece(Position(0, 0), rook)

        arbiter = _make_arbiter(1000)
        state = GameState()
        mov = Movement(frm=Position(0, 0), to=Position(0, 3),
                       piece=rook, start_ms=0, arrival_ms=3000)
        arbiter.register_motion(mov)

        proxy = arbiter.get_effective_board(board, state, 1000)
        # At t=1000, rook is at (0, 1) — origin should be empty
        self.assertIsNone(proxy.get_piece(Position(0, 0)))
        self.assertEqual(proxy.get_piece(Position(0, 1)), rook)


class TestArbiterCollisions(unittest.TestCase):
    def setUp(self) -> None:
        self.arbiter = _make_arbiter(ms_per_square=1000)
        self.board = ArrayBoard(8, 8)
        self.state = GameState()

    def _add_movement(self, piece, frm, to, start, arrival):
        self.board.set_piece(frm, piece)
        mov = Movement(frm=frm, to=to, piece=piece, start_ms=start, arrival_ms=arrival)
        self.arbiter.register_motion(mov)
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
        self.assertEqual(len(self.arbiter.movements()), 0)

    def test_swap_path_collision(self) -> None:
        p1 = Piece("w", "R")
        p2 = Piece("b", "R")
        m1 = self._add_movement(p1, Position(0, 0), Position(0, 2), 0, 2000)
        m2 = self._add_movement(p2, Position(0, 2), Position(0, 0), 0, 2000)

        # They will cross paths at (0, 1)
        self.state.clock_ms = 1000
        self.arbiter.resolve_movements(self.board, self.state, 1000)

        # Same start time -> tie-broken by registration order. p2 (registered
        # second) counts as the later arrival and captures p1, per the rule that
        # the later arrival eats the earlier one on an enemy collision.
        self.assertEqual(len(self.arbiter.movements()), 1)
        pieces_on_board = sum(1 for r in range(self.board.rows) for c in range(self.board.cols) if self.board.get_piece(Position(r, c)) is not None)
        self.assertEqual(pieces_on_board, 1)

    def test_later_start_wins_enemy_collision(self) -> None:
        p1 = Piece("w", "R")
        p2 = Piece("b", "R")
        # p1 starts first (0 ms) on a 2-square move through (0, 1).
        # p2 starts later (100 ms), approaching from a different row so it
        # doesn't block p1's path until it actually lands on (0, 1) at the
        # same tick p1 passes through.
        self._add_movement(p1, Position(0, 0), Position(0, 2), 0, 2000)
        self._add_movement(p2, Position(1, 1), Position(0, 1), 100, 1100)

        self.state.clock_ms = 1100
        self.arbiter.resolve_movements(self.board, self.state, 1100)

        # The later arrival (p2) eats the earlier one (p1).
        self.assertEqual(self.board.get_piece(Position(0, 1)), p2)
        self.assertIsNone(self.board.get_piece(Position(0, 0)))
        self.assertEqual(len(self.arbiter.movements()), 0)

    def test_same_color_later_start_gets_stuck(self) -> None:
        p1 = Piece("w", "R")
        p2 = Piece("w", "R")
        self._add_movement(p1, Position(0, 0), Position(0, 2), 0, 2000)
        self._add_movement(p2, Position(1, 1), Position(0, 1), 100, 1100)

        self.state.clock_ms = 1100
        self.arbiter.resolve_movements(self.board, self.state, 1100)

        # The later arrival (p2) is stuck in its previous square, not captured;
        # p1's own move is unaffected and keeps going toward (0, 2).
        remaining = self.arbiter.movements()
        self.assertEqual(len(remaining), 1)
        self.assertTrue(remaining[0].piece is p1)
        self.assertEqual(self.board.get_piece(Position(0, 0)), p1)
        self.assertEqual(self.board.get_piece(Position(1, 1)), p2)
        self.assertIsNone(self.board.get_piece(Position(0, 1)))
        self.assertEqual(len(self.state.active_cooldowns), 1)
        self.assertTrue(self.state.active_cooldowns[0].piece is p2)

    def test_same_color_staggered_arrival_first_stays_second_stuck(self) -> None:
        p1 = Piece("w", "R")
        p2 = Piece("w", "R")
        # p1 arrives and lands on (0, 2) well before p2 gets there — this is
        # not a same-tick collision, it's p2 arriving at an already-occupied
        # friendly square. p1 (identical type/color to p2) must not become
        # invisible to p2's landing check via a same-type equality mixup.
        self._add_movement(p1, Position(0, 0), Position(0, 2), 0, 2000)
        self._add_movement(p2, Position(0, 4), Position(0, 2), 0, 3000)

        for t in (2000, 3000):
            self.state.clock_ms = t
            self.arbiter.resolve_movements(self.board, self.state, t)

        # p1 (first arrival) keeps its position; p2 (second arrival) is
        # stopped one square short of the blocked destination — the last
        # square it actually reached before getting stuck — not sent back
        # to its origin.
        self.assertEqual(self.board.get_piece(Position(0, 2)), p1)
        self.assertEqual(self.board.get_piece(Position(0, 3)), p2)
        self.assertIsNone(self.board.get_piece(Position(0, 4)))
        self.assertEqual(len(self.arbiter.movements()), 0)

    def test_same_color_mid_flight_collision_stuck_at_collision_square(self) -> None:
        p1 = Piece("w", "R")
        p2 = Piece("w", "R")
        # p1 slides (0,0)->(0,2); p2 slides (2,1)->(0,1). At t=1000 (halfway
        # for both) their interpolated positions coincide at (1, 1) — a
        # mid-flight collision where neither piece has arrived nor started
        # from that square.
        self._add_movement(p1, Position(0, 0), Position(0, 2), 0, 2000)
        self._add_movement(p2, Position(2, 1), Position(0, 1), 0, 2000)

        self.state.clock_ms = 1000
        self.arbiter.resolve_movements(self.board, self.state, 1000)

        # p1 (early registration) keeps moving toward (0, 2). p2 (loser) is
        # stuck at the collision square (1, 1) — the last place it actually
        # reached — not sent back to its origin (2, 1).
        self.assertEqual(len(self.arbiter.movements()), 1)
        self.assertTrue(self.arbiter.movements()[0].piece is p1)
        self.assertEqual(self.board.get_piece(Position(1, 1)), p2)
        self.assertIsNone(self.board.get_piece(Position(2, 1)))
        self.assertEqual(len(self.state.active_cooldowns), 1)
        self.assertTrue(self.state.active_cooldowns[0].piece is p2)

    def test_ongoing_movement_cancelled_mid_flight_stuck_at_current_square(self) -> None:
        p1 = Piece("w", "R")
        p2 = Piece("w", "R")
        # p1 is a slow 4-square slide toward (0, 4). p2 is a faster 2-square
        # slide that reaches (0, 4) first and lands there while p1 is still
        # en route — this cancels p1's ongoing movement via the blocked-path
        # re-validation, not a same-tick collision or a final-arrival block.
        self._add_movement(p1, Position(0, 0), Position(0, 4), 0, 4000)
        self._add_movement(p2, Position(2, 4), Position(0, 4), 0, 2000)

        self.state.clock_ms = 2000
        self.arbiter.resolve_movements(self.board, self.state, 2000)

        # p2 lands on (0, 4). p1's move is cancelled at the tick it discovers
        # the block; p1 stops at (0, 2) — the square it had actually reached
        # by t=2000 — not back at its origin (0, 0).
        self.assertEqual(self.board.get_piece(Position(0, 4)), p2)
        self.assertEqual(self.board.get_piece(Position(0, 2)), p1)
        self.assertIsNone(self.board.get_piece(Position(0, 0)))
        self.assertEqual(len(self.arbiter.movements()), 0)
        self.assertTrue(any(c.piece is p1 for c in self.state.active_cooldowns))

    def test_jump_collision(self) -> None:
        p1 = Piece("w", "N")
        p2 = Piece("b", "R")
        # p1 jumps in place (frm == to) at (1, 2), ambushing the square.
        m1 = self._add_movement(p1, Position(1, 2), Position(1, 2), 0, 2000)
        p1.transition_to_jumping()
        m2 = self._add_movement(p2, Position(0, 2), Position(1, 2), 0, 2000)

        self.state.clock_ms = 2000
        self.arbiter.resolve_movements(self.board, self.state, 2000)

        # An ambushing (jump-in-place) piece always wins a same-square collision,
        # regardless of arrival order.
        self.assertEqual(self.board.get_piece(Position(1, 2)), p1)

    def test_collision_loser_source_square_not_cleared_if_occupied_by_other_piece(self) -> None:
        p1 = Piece("w", "R")
        p2 = Piece("b", "R")
        p3 = Piece("w", "P")

        # p2 moves from (0, 2) to (0, 0), starting at 0 ms
        m2 = self._add_movement(p2, Position(0, 2), Position(0, 0), 0, 2000)
        # p1 moves from (0, 0) to (0, 2), starting at 10 ms
        m1 = self._add_movement(p1, Position(0, 0), Position(0, 2), 10, 2010)

        # Now, replace the piece at p1's origin (0, 0) with p3
        self.board.set_piece(Position(0, 0), p3)

        # Advance clock to 1000. p1's own origin square no longer holds p1 (p3
        # sits there instead), so p1's ongoing movement gets cancelled/eliminated
        # by the blocked-path check — this must not clobber p3.
        self.state.clock_ms = 1000
        self.arbiter.resolve_movements(self.board, self.state, 1000)

        # Check that p3 (at (0, 0)) was NOT deleted from the board
        self.assertEqual(self.board.get_piece(Position(0, 0)), p3)

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
