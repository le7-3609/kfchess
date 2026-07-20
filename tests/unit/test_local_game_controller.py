"""LocalGameController: an in-memory match reported through the same seam.

The point of these is that nothing here starts a server, opens a socket, or
touches tkinter — the controller drives GameService directly and reports the
identical typed callbacks NetworkGameController produces from wire frames.
"""

from unittest.mock import MagicMock

import pytest

from shared.bootstrap import build_realtime_service
from shared.config import consts
from shared.events import GameEndedEvent, PieceCapturedEvent, PieceMovedEvent, ScoreUpdatedEvent
from shared.model.position import Position
from client.game_controller import NoticeLevel
from client.local_game_controller import LocalGameController


class _FakeClock:
    """A monotonic clock the test advances by hand, in seconds."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, ms: int) -> None:
        self.now += ms / consts.MS_PER_SECOND


@pytest.fixture
def started():
    """A started controller over a real service, plus its listener and clock."""
    clock = _FakeClock()
    controller = LocalGameController(service=build_realtime_service(), clock=clock)
    listener = MagicMock()
    controller.start(listener)
    return controller, listener, clock


def test_start_installs_the_board_and_announces_an_unowned_session(started):
    controller, listener, _ = started

    session = listener.on_session_started.call_args[0][0]
    assert session.assigned_color is None
    assert session.is_viewer is False

    snapshot = listener.on_snapshot.call_args[0][0]
    assert snapshot.rows == consts.DEFAULT_BOARD_ROWS
    assert snapshot.pieces


def test_poll_advances_the_simulation_clock_by_elapsed_wall_time(started):
    controller, listener, clock = started

    clock.advance_ms(250)
    controller.poll()

    assert listener.on_snapshot.call_args[0][0].clock_ms == 250


def test_a_submitted_move_reaches_the_board(started):
    """e2-e4 through the controller must land on e4 once the travel time elapses."""
    controller, listener, clock = started
    source, target = Position(6, 4), Position(4, 4)

    controller.submit_move(source, target)
    for _ in range(20):
        clock.advance_ms(200)
        controller.poll()

    snapshot = listener.on_snapshot.call_args[0][0]
    assert target in snapshot.pieces
    assert snapshot.pieces[target].piece_type == consts.PIECE_PAWN
    assert source not in snapshot.pieces


def test_events_are_buffered_until_poll_and_delivered_before_the_snapshot(started):
    """A capture must be recorded by the listener before it is handed the
    frame that capture already shows, or the flash lands a tick late."""
    controller, listener, _ = started
    order = []
    listener.on_capture.side_effect = lambda *a: order.append("capture")
    listener.on_snapshot.side_effect = lambda *a: order.append("snapshot")

    controller._buffer_event(PieceCapturedEvent(
            at_ms=10,
            pos=Position(4, 4),
            color="b",
            piece_type="P",
            captor_color="w",
            captor_piece_type="P",
        ))
    controller.poll()

    assert order == ["capture", "snapshot"]


def test_a_move_event_becomes_a_move_log_entry(started):
    controller, listener, _ = started

    controller._buffer_event(
        PieceMovedEvent(
            at_ms=1500,
            color=consts.COLOR_WHITE,
            piece_type="P",
            frm=Position(6, 4),
            to=Position(4, 4),
            was_capture=False,
        )
    )
    controller.poll()

    entry = listener.on_move_recorded.call_args[0][0]
    assert entry.color == consts.COLOR_WHITE
    assert entry.notation == "Pe2-e4"
    assert entry.time_ms == 1500


def test_a_score_event_is_forwarded(started):
    controller, listener, _ = started

    controller._buffer_event(ScoreUpdatedEvent(at_ms=0, white_score=3, black_score=1))
    controller.poll()

    listener.on_score_changed.assert_called_with(3, 1)


def test_game_over_is_a_terminal_notice_naming_the_winning_seat(started):
    """With no seat assigned, an offline result is phrased by color rather
    than as a win or a loss — neither player "is" the window."""
    controller, listener, _ = started

    controller._buffer_event(
        GameEndedEvent(at_ms=0, reason=consts.GAME_OVER_CHECKMATE, winner=consts.COLOR_WHITE)
    )
    controller.poll()

    notice = listener.on_notice.call_args[0][0]
    assert notice.level is NoticeLevel.TERMINAL
    assert "White" in notice.text


def test_game_over_against_a_bot_is_phrased_as_a_win_or_a_loss():
    clock = _FakeClock()
    controller = LocalGameController(
        service=build_realtime_service(), assigned_color=consts.COLOR_WHITE, clock=clock
    )
    listener = MagicMock()
    controller.start(listener)

    controller._buffer_event(
        GameEndedEvent(at_ms=0, reason=consts.GAME_OVER_CHECKMATE, winner=consts.COLOR_BLACK)
    )
    controller.poll()

    assert "you lose" in listener.on_notice.call_args[0][0].text.lower()


def test_the_clock_stops_once_the_game_has_ended(started):
    """The final frame's clock is what capture flashes expire against; letting
    it run on would age the last thing the player sees out from under them."""
    controller, listener, clock = started

    controller._buffer_event(GameEndedEvent(at_ms=0, reason=consts.GAME_OVER_CHECKMATE, winner=None))
    controller.poll()
    frozen_at = listener.on_snapshot.call_args[0][0].clock_ms

    clock.advance_ms(1000)
    controller.poll()

    assert listener.on_snapshot.call_args[0][0].clock_ms == frozen_at


def test_leaving_detaches_from_the_bus_and_is_safe_to_repeat(started):
    controller, listener, clock = started

    controller.leave()
    controller.leave()

    listener.reset_mock()
    clock.advance_ms(100)
    controller.poll()
    listener.on_snapshot.assert_not_called()


def test_an_offline_match_offers_jump_preferences_and_history(started):
    """Capability flags the window reads before binding the controls; all
    three are things only a locally-owned simulation can honour."""
    controller, _, _ = started

    assert controller.supports_jump is True
    assert controller.supports_preferences is True
    assert controller.history is not None
