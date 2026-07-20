"""Unit tests for MaterialScoreTracker — captured-material totals derived from events.

The tracker never reads the board, so these drive it purely through the bus.
"""

import unittest

from shared.events import (
    Event,
    EventBus,
    GameStartedEvent,
    Observer,
    PieceCapturedEvent,
    ScoreUpdatedEvent,
)
from shared.model.position import Position
from shared.scoring import MaterialScoreTracker


def _capture(piece_type: str, color: str = "b", captor_color: str = "w") -> PieceCapturedEvent:
    return PieceCapturedEvent(
        at_ms=100, color=color, piece_type=piece_type, pos=Position(0, 0),
        captor_color=captor_color, captor_piece_type="R",
    )


class _ScoreRecorder(Observer):
    def __init__(self) -> None:
        self.updates: list = []

    def on_event(self, event: Event) -> None:
        if isinstance(event, ScoreUpdatedEvent):
            self.updates.append((event.white_score, event.black_score))


class TestMaterialScoreTracker(unittest.TestCase):
    def setUp(self):
        self.bus = EventBus()
        self.tracker = MaterialScoreTracker(self.bus)
        self.bus.subscribe(self.tracker, PieceCapturedEvent, GameStartedEvent)
        self.recorder = _ScoreRecorder()
        self.bus.subscribe(self.recorder, ScoreUpdatedEvent)

    def test_starts_at_zero(self):
        self.assertEqual(self.tracker.score_for("w"), 0)
        self.assertEqual(self.tracker.score_for("b"), 0)

    def test_credits_the_captor_with_the_victims_value(self):
        self.bus.publish(_capture("Q"))
        self.assertEqual(self.tracker.score_for("w"), 9)
        self.assertEqual(self.tracker.score_for("b"), 0)

    def test_accumulates_across_captures(self):
        self.bus.publish(_capture("P"))
        self.bus.publish(_capture("N"))
        self.assertEqual(self.tracker.score_for("w"), 4)

    def test_tracks_both_colors_independently(self):
        self.bus.publish(_capture("R", color="b", captor_color="w"))
        self.bus.publish(_capture("B", color="w", captor_color="b"))
        self.assertEqual(self.tracker.score_for("w"), 5)
        self.assertEqual(self.tracker.score_for("b"), 3)

    def test_friendly_fire_scores_nothing(self):
        """Real-time collisions let a side take out its own piece; that is a
        loss for its owner, not a gain for anyone."""
        self.bus.publish(_capture("Q", color="w", captor_color="w"))
        self.assertEqual(self.tracker.score_for("w"), 0)
        self.assertEqual(self.recorder.updates, [])

    def test_a_captured_king_adds_nothing(self):
        """The game is over either way, so a king's material value never counts."""
        self.bus.publish(_capture("K"))
        self.assertEqual(self.tracker.score_for("w"), 0)

    def test_an_unknown_piece_type_is_worth_nothing(self):
        self.bus.publish(_capture("Z"))
        self.assertEqual(self.tracker.score_for("w"), 0)

    def test_publishes_the_running_total_on_each_capture(self):
        self.bus.publish(_capture("P"))
        self.bus.publish(_capture("R"))
        self.assertEqual(self.recorder.updates, [(1, 0), (6, 0)])

    def test_a_new_game_resets_and_announces_zero(self):
        self.bus.publish(_capture("Q"))
        self.bus.publish(GameStartedEvent(at_ms=0, rows=8, cols=8))
        self.assertEqual(self.tracker.score_for("w"), 0)
        self.assertEqual(self.recorder.updates[-1], (0, 0))


if __name__ == "__main__":
    unittest.main()
