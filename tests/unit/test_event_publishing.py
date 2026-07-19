"""Tests that the simulation publishes the events the UI is built to observe.

These drive a real GameService rather than the resolvers directly: the point is
that a subscriber attached at the facade — as the tk window is — actually hears
about moves, captures, aborts and endings, with the coordinates and timings it
needs to draw them.
"""

import unittest
from typing import List, Type

from kungfu_chess.bootstrap import build_realtime_service, build_service
from kungfu_chess.events import (
    ABORT_REASON_FRIENDLY_COLLISION,
    Event,
    GameEndedEvent,
    GameStartedEvent,
    MoveAbortedEvent,
    MoveStartedEvent,
    Observer,
    PieceCapturedEvent,
    PieceMovedEvent,
    PiecePromotedEvent,
)
from kungfu_chess.model.position import Position


class _Recorder(Observer):
    def __init__(self) -> None:
        self.seen: List[Event] = []

    def on_event(self, event: Event) -> None:
        self.seen.append(event)

    def of(self, event_type: Type[Event]) -> List[Event]:
        return [e for e in self.seen if isinstance(e, event_type)]

    def one(self, event_type: Type[Event]) -> Event:
        matches = self.of(event_type)
        assert len(matches) == 1, f"expected exactly one {event_type.__name__}, got {len(matches)}"
        return matches[0]


def _service_with(board: str, recorder: _Recorder, realtime: bool = False, ms_per_square: int = 100):
    """Install *board* on a service with *recorder* already subscribed."""
    if realtime:
        service = build_realtime_service(require_kings=False, ms_per_square=ms_per_square)
    else:
        service = build_service(require_kings=False)
    service.subscribe(recorder)
    result = service.init_game(board.strip("\n").splitlines())
    assert result.is_ok, result.error
    return service


def _run(service, ms: int, step: int = 25) -> None:
    for _ in range(ms // step):
        service.advance_clock(step)


ROOK_TAKES_PAWN = """
Board:
. . . .
. bP . .
. . . .
. wR . .
"""

ROOK_TAKES_KING = """
Board:
. . . .
. bK . .
. . . .
. wR . .
"""

PAWN_ABOUT_TO_PROMOTE = """
Board:
. . . .
wP . . .
. . . .
. . . .
"""

# Rook and bishop are both two squares from (2, 2), so they arrive together and
# collide. Deliberately two different piece types: TextPiece equality is by
# colour+type, so two same-type friendly pieces are indistinguishable to the
# arbiter's has_active_motion check and the second could never set off.
CONVERGING_FRIENDLY_PIECES = """
Board:
. . . . .
. . . . .
wR . . . .
. . . . .
wB . . . .
"""


class TestMoveEvents(unittest.TestCase):
    def test_starting_a_move_announces_its_route_and_arrival(self):
        recorder = _Recorder()
        service = _service_with(ROOK_TAKES_PAWN, recorder, realtime=True)
        service.click(3, 1)
        service.click(1, 1)

        started = recorder.one(MoveStartedEvent)
        self.assertEqual(started.frm, Position(3, 1))
        self.assertEqual(started.to, Position(1, 1))
        self.assertEqual(started.color, "w")
        self.assertGreater(started.arrival_ms, started.at_ms)

    def test_a_completed_move_is_announced_on_arrival(self):
        recorder = _Recorder()
        service = _service_with(ROOK_TAKES_PAWN, recorder, realtime=True)
        service.click(3, 1)
        service.click(1, 1)
        _run(service, 400)

        moved = recorder.one(PieceMovedEvent)
        self.assertEqual((moved.frm, moved.to), (Position(3, 1), Position(1, 1)))
        self.assertTrue(moved.was_capture)

    def test_a_move_is_stamped_with_its_arrival_not_the_tick_it_was_noticed_in(self):
        """Travel is 2 squares at 100ms each, so it lands at 200ms however
        coarsely the caller happens to be advancing the clock."""
        recorder = _Recorder()
        service = _service_with(ROOK_TAKES_PAWN, recorder, realtime=True)
        service.click(3, 1)
        service.click(1, 1)
        service.advance_clock(1000)

        self.assertEqual(recorder.one(PieceMovedEvent).at_ms, 200)


class TestCaptureEvents(unittest.TestCase):
    def test_a_capture_names_victim_captor_and_square(self):
        recorder = _Recorder()
        service = _service_with(ROOK_TAKES_PAWN, recorder)
        service.click(3, 1)
        service.click(1, 1)

        captured = recorder.one(PieceCapturedEvent)
        self.assertEqual((captured.color, captured.piece_type), ("b", "P"))
        self.assertEqual((captured.captor_color, captured.captor_piece_type), ("w", "R"))
        self.assertEqual(captured.pos, Position(1, 1))

    def test_a_quiet_move_announces_no_capture(self):
        recorder = _Recorder()
        service = _service_with(ROOK_TAKES_PAWN, recorder)
        service.click(3, 1)
        service.click(2, 1)

        self.assertEqual(recorder.of(PieceCapturedEvent), [])
        self.assertFalse(recorder.one(PieceMovedEvent).was_capture)


class TestGameEndedEvents(unittest.TestCase):
    def test_capturing_a_king_ends_the_game_with_the_captor_as_winner(self):
        recorder = _Recorder()
        service = _service_with(ROOK_TAKES_KING, recorder)
        service.click(3, 1)
        service.click(1, 1)

        ended = recorder.one(GameEndedEvent)
        self.assertEqual(ended.reason, "king_captured")
        self.assertEqual(ended.winner, "w")

    def test_the_ending_is_announced_exactly_once_however_long_play_continues(self):
        """The UI prompts to save on this event, so a repeat would re-prompt."""
        recorder = _Recorder()
        service = _service_with(ROOK_TAKES_KING, recorder, realtime=True)
        service.click(3, 1)
        service.click(1, 1)
        _run(service, 2000)

        self.assertEqual(len(recorder.of(GameEndedEvent)), 1)

    def test_the_capture_is_announced_before_the_ending_it_caused(self):
        recorder = _Recorder()
        service = _service_with(ROOK_TAKES_KING, recorder)
        service.click(3, 1)
        service.click(1, 1)

        self.assertLess(
            recorder.seen.index(recorder.one(PieceCapturedEvent)),
            recorder.seen.index(recorder.one(GameEndedEvent)),
        )


class TestPromotionEvents(unittest.TestCase):
    def test_promotion_reports_both_the_old_and_new_type(self):
        recorder = _Recorder()
        service = _service_with(PAWN_ABOUT_TO_PROMOTE, recorder)
        service.click(1, 0)
        service.click(0, 0)

        promoted = recorder.one(PiecePromotedEvent)
        self.assertEqual(promoted.from_piece_type, "P")
        self.assertEqual(promoted.to_piece_type, "Q")
        self.assertEqual(promoted.pos, Position(0, 0))

    def test_the_move_that_promoted_reports_the_promoted_type(self):
        recorder = _Recorder()
        service = _service_with(PAWN_ABOUT_TO_PROMOTE, recorder)
        service.click(1, 0)
        service.click(0, 0)

        self.assertEqual(recorder.one(PieceMovedEvent).piece_type, "Q")


class TestAbortEvents(unittest.TestCase):
    def test_a_friendly_collision_announces_the_abort_and_where_it_stopped(self):
        recorder = _Recorder()
        service = _service_with(CONVERGING_FRIENDLY_PIECES, recorder, realtime=True)
        service.click(2, 0)
        service.click(2, 2)
        service.click(4, 0)
        service.click(2, 2)
        _run(service, 400)

        aborted = recorder.of(MoveAbortedEvent)
        self.assertTrue(aborted, "a friendly collision should announce an abort")
        self.assertEqual(aborted[0].reason, ABORT_REASON_FRIENDLY_COLLISION)
        self.assertEqual(aborted[0].color, "w")

    def test_neither_piece_is_captured_by_its_own_side(self):
        recorder = _Recorder()
        service = _service_with(CONVERGING_FRIENDLY_PIECES, recorder, realtime=True)
        service.click(2, 0)
        service.click(2, 2)
        service.click(4, 0)
        service.click(2, 2)
        _run(service, 400)

        self.assertEqual(recorder.of(PieceCapturedEvent), [])


class TestGameStartedEvent(unittest.TestCase):
    def test_installing_a_board_announces_its_size(self):
        recorder = _Recorder()
        _service_with(ROOK_TAKES_PAWN, recorder)

        started = recorder.one(GameStartedEvent)
        self.assertEqual((started.rows, started.cols), (4, 4))


class TestSubscriptionThroughTheFacade(unittest.TestCase):
    def test_a_filtered_subscriber_only_hears_its_own_types(self):
        recorder = _Recorder()
        service = build_service(require_kings=False)
        service.subscribe(recorder, PieceCapturedEvent)
        service.init_game(ROOK_TAKES_PAWN.strip("\n").splitlines())
        service.click(3, 1)
        service.click(1, 1)

        self.assertEqual([type(e) for e in recorder.seen], [PieceCapturedEvent])

    def test_unsubscribing_stops_delivery(self):
        recorder = _Recorder()
        service = _service_with(ROOK_TAKES_PAWN, recorder)
        service.unsubscribe(recorder)
        recorder.seen.clear()
        service.click(3, 1)
        service.click(1, 1)

        self.assertEqual(recorder.seen, [])

    def test_a_service_built_without_a_bus_refuses_subscription(self):
        from kungfu_chess.service import GameService

        service = GameService(
            board_repo=None, state_repo=None, parser=None, validator=None, engine=None
        )
        with self.assertRaises(RuntimeError):
            service.subscribe(_Recorder())


if __name__ == "__main__":
    unittest.main()
