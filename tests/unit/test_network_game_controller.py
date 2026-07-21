"""NetworkGameController: wire frames in, typed listener callbacks out.

These assertions used to live against NetworkedGameWindow, because the window
decoded the protocol itself. They moved with the decoding: the window now only
receives GameSnapshot/MoveLogEntry/GameNotice, so everything about frame
shapes belongs here, where no tkinter root is involved at all.
"""

from unittest.mock import MagicMock

from shared.config import consts
from shared.model.position import Position
from client.controllers.game_controller import NoticeLevel
from client.controllers.network_game_controller import NetworkGameController
from client.network.network_client import (
    MSG_TYPE_CONNECTION_STATUS,
    STATUS_DISCONNECTED,
    STATUS_RECONNECT_FAILED,
    STATUS_RECONNECTING,
)


def _controller(assigned_color=None):
    """A started controller plus the listener it reports to."""
    controller = NetworkGameController(network_client=MagicMock(), username="tester")
    listener = MagicMock()
    controller.start(listener)
    if assigned_color is not None:
        controller._assigned_color = assigned_color
    return controller, listener


def _only_notice(listener):
    listener.on_notice.assert_called_once()
    return listener.on_notice.call_args[0][0]


def _last_notice(listener):
    return listener.on_notice.call_args[0][0]


def test_start_redirects_the_running_client_instead_of_restarting_it():
    """Regression: the lobby hands over a NetworkClient it already started.

    Calling `.start()` again would raise, so the controller must redirect the
    running client's callback onto its own inbox — otherwise frames keep
    flowing to the queue the client was originally started with and the board
    never renders.
    """
    client = MagicMock()
    controller = NetworkGameController(network_client=client, username="tester")

    controller.start(MagicMock())

    client.start.assert_not_called()
    client.set_message_callback.assert_called_once()


def test_frames_are_decoded_only_on_poll_and_in_arrival_order():
    """NetworkClient delivers on its socket thread; nothing may reach the
    listener until the UI thread asks for it."""
    controller, listener = _controller()

    controller.accept_frame(
        {"type": "event_score_updated", "white_score": 1, "black_score": 0}
    )
    controller.accept_frame(
        {"type": "event_score_updated", "white_score": 2, "black_score": 0}
    )
    listener.on_score_changed.assert_not_called()

    controller.poll()

    assert [c.args for c in listener.on_score_changed.call_args_list] == [(1, 0), (2, 0)]


def test_game_start_assigns_the_seat_and_names_the_opponent():
    controller, listener = _controller()

    controller.accept_frame(
        {
            "type": "game_start",
            "color": consts.COLOR_BLACK,
            "opponent": "Alice",
            "room_id": "AB12",
        }
    )
    controller.poll()

    session = listener.on_session_started.call_args[0][0]
    assert session.assigned_color == consts.COLOR_BLACK
    assert session.opponent_name == "Alice"
    assert session.room_id == "AB12"
    assert session.is_viewer is False
    assert controller.assigned_color == consts.COLOR_BLACK


def test_game_start_as_viewer_reports_no_seat():
    controller, listener = _controller()

    controller.accept_frame({"type": "game_start", "color": "viewer", "opponent": "Alice"})
    controller.poll()

    session = listener.on_session_started.call_args[0][0]
    assert session.is_viewer is True
    assert session.assigned_color is None
    assert controller.is_viewer is True


def test_room_created_announces_the_wait_for_an_opponent():
    controller, listener = _controller()

    controller.accept_frame({"type": "room_created", "room_id": "AB12"})
    controller.poll()

    session = listener.on_session_started.call_args[0][0]
    assert session.room_id == "AB12"
    assert session.assigned_color is None

    notice = _only_notice(listener)
    assert notice.level is NoticeLevel.TRANSIENT
    assert "AB12" in notice.text


def test_piece_moved_becomes_a_move_log_entry():
    controller, listener = _controller()

    controller.accept_frame(
        {
            "type": "event_piece_moved",
            "color": consts.COLOR_WHITE,
            "piece_type": "P",
            "from": "e2",
            "to": "e4",
            "at_ms": 1500,
        }
    )
    controller.poll()

    entry = listener.on_move_recorded.call_args[0][0]
    assert entry.color == consts.COLOR_WHITE
    assert entry.notation == "Pe2-e4"
    assert entry.time_ms == 1500


def test_piece_captured_becomes_a_board_position():
    controller, listener = _controller()

    controller.accept_frame({"type": "event_piece_captured", "pos": "e4", "at_ms": 900})
    controller.poll()

    listener.on_capture.assert_called_once_with(Position(4, 4), 900)


def test_submit_move_sends_algebraic_squares():
    controller, _ = _controller()

    controller.submit_move(Position(6, 4), Position(4, 4))

    controller._network_client.send_move.assert_called_once_with("e2", "e4")


def test_opponent_disconnected_shows_the_countdown_with_the_opponents_name():
    controller, listener = _controller()

    controller.accept_frame(
        {"type": "opponent_disconnected", "username": "Bob", "countdown_seconds": 30}
    )
    controller.poll()

    notice = _only_notice(listener)
    assert notice.level is NoticeLevel.TRANSIENT
    assert "Bob" in notice.text
    assert "30" in notice.text


def test_countdown_tick_reuses_the_remembered_opponent_name():
    """`countdown_tick` carries no username of its own."""
    controller, listener = _controller()

    controller.accept_frame(
        {"type": "opponent_disconnected", "username": "Bob", "countdown_seconds": 30}
    )
    controller.accept_frame({"type": "countdown_tick", "seconds_remaining": 12})
    controller.poll()

    notice = _last_notice(listener)
    assert "Bob" in notice.text
    assert "12" in notice.text


def test_opponent_reconnected_clears_the_notice():
    controller, listener = _controller()

    controller.accept_frame({"type": "opponent_reconnected", "username": "Bob"})
    controller.poll()

    assert _only_notice(listener).level is NoticeLevel.CLEARED


def test_forfeit_victory_is_terminal():
    controller, listener = _controller()

    controller.accept_frame({"type": "forfeit_victory"})
    controller.poll()

    notice = _only_notice(listener)
    assert notice.level is NoticeLevel.TERMINAL
    assert "win" in notice.text.lower()
    assert notice.outcome is True


def test_game_end_shows_the_winners_new_rating():
    """The White winner reads its own rating change out of the "white" key,
    not the loser's — this is the parsing the ELO sync depends on."""
    controller, listener = _controller(assigned_color=consts.COLOR_WHITE)

    controller.accept_frame(
        {
            "type": "game_end",
            "reason": "checkmate",
            "winner": consts.COLOR_WHITE,
            "white": {"new_elo": 1215, "elo_change": 15},
            "black": {"new_elo": 1185, "elo_change": -15},
        }
    )
    controller.poll()

    notice = _only_notice(listener)
    assert notice.level is NoticeLevel.TERMINAL
    assert "win" in notice.text.lower()
    assert "1215" in notice.text
    assert "+15" in notice.text
    assert notice.outcome is True


def test_game_end_shows_the_losers_new_rating():
    controller, listener = _controller(assigned_color=consts.COLOR_BLACK)

    controller.accept_frame(
        {
            "type": "game_end",
            "reason": "checkmate",
            "winner": consts.COLOR_WHITE,
            "white": {"new_elo": 1215, "elo_change": 15},
            "black": {"new_elo": 1185, "elo_change": -15},
        }
    )
    controller.poll()

    notice = _only_notice(listener)
    text = notice.text
    assert "lose" in text.lower()
    assert "1185" in text
    assert "-15" in text
    assert notice.outcome is False


def test_game_end_draw_with_no_rating_payload_shows_result_only():
    """An unrated game (no database, or a bot involved) omits both rating
    keys entirely — the handler must not choke looking one up."""
    controller, listener = _controller(assigned_color=consts.COLOR_WHITE)

    controller.accept_frame({"type": "game_end", "reason": "stalemate", "winner": None})
    controller.poll()

    notice = _only_notice(listener)
    text = notice.text
    assert "draw" in text.lower()
    assert "rating" not in text.lower()
    assert notice.outcome is None


def test_game_end_after_forfeit_credits_the_win_and_the_rating():
    """A rated forfeit's game_end frame supersedes the plain forfeit_victory
    text with a message that names the reason and the rating change."""
    controller, listener = _controller(assigned_color=consts.COLOR_BLACK)

    controller.accept_frame(
        {
            "type": "game_end",
            "reason": "disconnection_timeout",
            "winner": consts.COLOR_BLACK,
            "white": {"new_elo": 1185, "elo_change": -15},
            "black": {"new_elo": 1215, "elo_change": 15},
        }
    )
    controller.poll()

    notice = _only_notice(listener)
    text = notice.text
    assert "forfeited" in text.lower()
    assert "win" in text.lower()
    assert "1215" in text
    assert notice.outcome is True


def test_connection_status_drives_transient_then_terminal_notices():
    controller, listener = _controller()

    controller.accept_frame(
        {"type": MSG_TYPE_CONNECTION_STATUS, "status": STATUS_DISCONNECTED}
    )
    controller.poll()
    assert _last_notice(listener).level is NoticeLevel.TRANSIENT

    controller.accept_frame(
        {
            "type": MSG_TYPE_CONNECTION_STATUS,
            "status": STATUS_RECONNECTING,
            "attempt": 2,
            "delay_seconds": 4.0,
        }
    )
    controller.poll()
    reconnecting = _last_notice(listener)
    assert reconnecting.level is NoticeLevel.TRANSIENT
    assert "2" in reconnecting.text

    controller.accept_frame(
        {"type": MSG_TYPE_CONNECTION_STATUS, "status": STATUS_RECONNECT_FAILED}
    )
    controller.poll()
    assert _last_notice(listener).level is NoticeLevel.TERMINAL


def test_a_networked_seat_offers_neither_jump_nor_preferences_nor_history():
    """Capability flags the window reads before binding controls the server
    has no frame for."""
    controller, _ = _controller()

    assert controller.supports_jump is False
    assert controller.supports_preferences is False
    assert controller.history is None
