"""Unit tests for replay reconstruction — the pure timeline logic behind the
replay window. No tkinter here; ReplayDirector is a function of the save alone.
"""

import unittest
from unittest.mock import Mock

from kungfu_chess.config.game_config import GameConfig
from kungfu_chess.io.game_history_store import SavedGame
from kungfu_chess.io.moves_log import MoveLogEntry, parse_notation
from kungfu_chess.io.replay import ReplayEngineDecorator
from kungfu_chess.model.position import Position
from kungfu_chess.ui.window.replay_window import ReplayDirector, reconstruct_moves
from kungfu_chess.view.piece_visual_state import PieceVisualState

E2, E4 = Position(6, 4), Position(4, 4)
D7, D5 = Position(1, 3), Position(3, 3)


def _saved(moves, speed_ms=1000, cooldown_ms=1000, winner=None) -> SavedGame:
    return SavedGame(
        save_name="test",
        white_name="W",
        black_name="B",
        winner=winner,
        saved_at="2026-01-01_00-00-00",
        moves=moves,
        speed_ms=speed_ms,
        cooldown_ms=cooldown_ms,
    )


def _entry(notation, time_ms, color="w") -> MoveLogEntry:
    return MoveLogEntry(color=color, notation=notation, time_ms=time_ms)


class TestParseNotation(unittest.TestCase):
    def test_round_trips_a_logged_move(self):
        parsed = parse_notation("Pe2-e4")
        self.assertEqual(parsed.piece_type, "P")
        self.assertEqual(parsed.frm, E2)
        self.assertEqual(parsed.to, E4)

    def test_returns_none_for_junk(self):
        for junk in ("", "hello", "Xe2-e4", "Pe2e4", "Pe9-e4"):
            self.assertIsNone(parse_notation(junk), junk)


class TestReconstructMoves(unittest.TestCase):
    def test_derives_start_from_arrival_and_distance(self):
        # e2-e4 is two squares, so at 1000ms/square it left 2000ms before arriving.
        moves = reconstruct_moves(_saved([_entry("Pe2-e4", 5000)]), GameConfig())
        self.assertEqual(moves[0].start_ms, 3000)
        self.assertEqual(moves[0].arrival_ms, 5000)

    def test_uses_the_speed_the_save_recorded(self):
        moves = reconstruct_moves(_saved([_entry("Pe2-e4", 5000)], speed_ms=500), GameConfig())
        self.assertEqual(moves[0].start_ms, 4000)

    def test_jump_in_place_uses_jump_duration_not_distance(self):
        # frm == to covers no distance; a distance-based duration would make it
        # instantaneous and so invisible.
        config = GameConfig()
        config.jump_duration_ms = 800
        moves = reconstruct_moves(_saved([_entry("Pe4-e4", 5000)]), config)
        self.assertTrue(moves[0].is_jump)
        self.assertEqual(moves[0].start_ms, 4200)

    def test_orders_by_arrival(self):
        moves = reconstruct_moves(
            _saved([_entry("Pe2-e4", 9000), _entry("Pd7-d5", 3000, color="b")]), GameConfig()
        )
        self.assertEqual([m.arrival_ms for m in moves], [3000, 9000])

    def test_drops_unparseable_entries_rather_than_raising(self):
        moves = reconstruct_moves(_saved([_entry("garbage", 1000), _entry("Pe2-e4", 2000)]), GameConfig())
        self.assertEqual(len(moves), 1)


class TestSnapshotAt(unittest.TestCase):
    def test_starts_from_the_standard_opening_setup(self):
        snapshot = ReplayDirector(_saved([])).snapshot_at(0)
        self.assertEqual(len(snapshot.pieces), 32)
        self.assertEqual(snapshot.piece_at(E2).piece_type, "P")
        self.assertEqual(snapshot.piece_at(E2).color, "w")

    def test_piece_has_not_left_its_origin_before_the_move_starts(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 5000)]))
        snapshot = director.snapshot_at(1000)
        self.assertIsNotNone(snapshot.piece_at(E2))
        self.assertEqual(snapshot.active_movements, ())

    def test_piece_is_in_flight_between_start_and_arrival(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 5000)]))
        snapshot = director.snapshot_at(4000)  # started at 3000, arrives at 5000

        self.assertEqual(len(snapshot.active_movements), 1)
        movement = snapshot.active_movements[0]
        self.assertEqual((movement.frm, movement.to), (E2, E4))
        self.assertEqual((movement.start_ms, movement.arrival_ms), (3000, 5000))
        # The renderer interpolates from frm, so the piece stays there meanwhile.
        self.assertIsNotNone(snapshot.piece_at(E2))
        self.assertIsNone(snapshot.piece_at(E4))
        self.assertEqual(snapshot.piece_at(E2).state, PieceVisualState.MOVE)

    def test_piece_has_landed_after_arrival(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 5000)]))
        snapshot = director.snapshot_at(5000)
        self.assertIsNone(snapshot.piece_at(E2))
        self.assertEqual(snapshot.piece_at(E4).piece_type, "P")
        self.assertEqual(snapshot.active_movements, ())

    def test_landed_piece_rests_then_goes_idle(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 5000)], cooldown_ms=1000))
        self.assertEqual(director.snapshot_at(5500).piece_at(E4).state, PieceVisualState.SHORT_REST)
        self.assertEqual(director.snapshot_at(6500).piece_at(E4).state, PieceVisualState.IDLE)

    def test_capture_removes_the_occupant(self):
        director = ReplayDirector(
            _saved([_entry("Pe2-e4", 2000), _entry("Pd7-d5", 4000, color="b"), _entry("Pe4-d5", 6000)])
        )
        snapshot = director.snapshot_at(6000)
        self.assertEqual(snapshot.piece_at(D5).color, "w")
        self.assertIsNone(snapshot.piece_at(E4))

    def test_seeking_backwards_matches_playing_forwards(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 2000), _entry("Pd7-d5", 4000, color="b")]))
        forwards = director.snapshot_at(3000)
        director.snapshot_at(9000)
        backwards = director.snapshot_at(3000)
        self.assertEqual(dict(backwards.pieces), dict(forwards.pieces))

    def test_replay_ends_after_a_pad_past_the_last_arrival(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 5000)]))
        self.assertGreater(director.duration_ms, 5000)

    def test_side_panel_only_shows_moves_already_played(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 2000), _entry("Pd7-d5", 8000, color="b")]))
        self.assertEqual(len(director.moves_until(3000)), 1)
        self.assertEqual(len(director.moves_until(8000)), 2)


class TestPromotion(unittest.TestCase):
    """A promoting pawn is swapped before the move event fires, so it is logged
    arriving as its promoted type from a square holding a pawn."""

    def test_pawn_logged_as_queen_arrives_as_a_queen(self):
        a7, a8 = Position(1, 0), Position(0, 0)
        director = ReplayDirector(
            _saved([_entry("Pa2-a7", 5000), _entry("Qa7-a8", 8000)])
        )
        # The mover is found by origin square, not by the notation's type letter.
        in_flight = director.snapshot_at(7000)
        self.assertIsNotNone(in_flight.piece_at(a7))
        self.assertEqual(len(in_flight.active_movements), 1)

        landed = director.snapshot_at(8000)
        self.assertEqual(landed.piece_at(a8).piece_type, "Q")
        self.assertEqual(landed.piece_at(a8).color, "w")


class TestEnPassant(unittest.TestCase):
    def test_removes_the_pawn_captured_in_passing(self):
        # White pawn to e5; black pawn d7-d5 alongside it; white takes on d6.
        e5, d5, d6 = Position(3, 4), Position(3, 3), Position(2, 3)
        director = ReplayDirector(
            _saved(
                [
                    _entry("Pe2-e5", 3000),
                    _entry("Pd7-d5", 5000, color="b"),
                    _entry("Pe5-d6", 7000),
                ]
            )
        )
        snapshot = director.snapshot_at(7000)
        self.assertEqual(snapshot.piece_at(d6).color, "w")
        # The victim stands beside the capturer and is never named by the log.
        self.assertIsNone(snapshot.piece_at(d5), "pawn taken en passant should be gone")
        self.assertIsNone(snapshot.piece_at(e5))


class TestLogGaps(unittest.TestCase):
    """The log holds only resolved arrivals, so some moves reference squares the
    replay cannot account for. These must degrade, never crash."""

    def test_move_from_an_empty_square_is_skipped(self):
        director = ReplayDirector(_saved([_entry("Rh4-h6", 3000)]))  # nothing on h4
        snapshot = director.snapshot_at(3000)
        self.assertEqual(len(snapshot.pieces), 32)

    def test_move_by_the_wrong_colour_is_skipped(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 3000, color="b")]))
        snapshot = director.snapshot_at(3000)
        self.assertIsNotNone(snapshot.piece_at(E2))


class TestGameOverBanner(unittest.TestCase):
    def test_finished_game_shows_the_winner_at_the_end(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 2000)], winner="w"))
        self.assertFalse(director.snapshot_at(2000).game_over)
        self.assertTrue(director.snapshot_at(director.duration_ms).game_over)
        self.assertEqual(director.snapshot_at(director.duration_ms).winner, "w")

    def test_mid_game_save_never_shows_a_banner(self):
        director = ReplayDirector(_saved([_entry("Pe2-e4", 2000)], winner=None))
        self.assertFalse(director.snapshot_at(director.duration_ms).game_over)


class TestReplayEngineDecoratorAdvanceClock(unittest.TestCase):
    """ReplayEngineDecorator only overrides execute_command; every other
    GameEngine call — advance_clock included — must reach the wrapped
    engine, since GameService.advance_clock calls it directly."""

    def test_advance_clock_forwards_to_the_wrapped_engine(self):
        engine = Mock()
        decorator = ReplayEngineDecorator(engine, writer=Mock())

        decorator.advance_clock(500)

        engine.advance_clock.assert_called_once_with(500)

    def test_other_missing_attributes_also_forward_via_getattr(self):
        engine = Mock()
        decorator = ReplayEngineDecorator(engine, writer=Mock())

        decorator.legal_moves_from(Position(6, 4))

        engine.legal_moves_from.assert_called_once_with(Position(6, 4))


if __name__ == "__main__":
    unittest.main()
