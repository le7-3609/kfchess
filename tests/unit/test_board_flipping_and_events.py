from unittest.mock import MagicMock
from shared.config import consts
from client.ui.rendering.board_geometry import BoardGeometry
from client.ui.window.networked_game_window import NetworkedGameWindow


def test_board_geometry_flipping():
    geom = BoardGeometry(rows=8, cols=8)
    geom.resize(800, 800)

    # Standard (unflipped)
    rect_a8 = geom.cell_to_pixel(0, 0)
    assert rect_a8.x == 0
    assert rect_a8.y == 0

    assert geom.pixel_to_cell(10, 10) == (0, 0)

    # Flipped (Black player)
    geom.set_flipped(True)

    # In 8x8 flipped: row=0, col=0 (a8) maps to display_row=7, display_col=7 (bottom-right)
    rect_a8_flipped = geom.cell_to_pixel(0, 0)
    assert rect_a8_flipped.x == 700
    assert rect_a8_flipped.y == 700

    # pixel_to_cell at (710, 710) maps back to cell (0, 0)
    assert geom.pixel_to_cell(710, 710) == (0, 0)

    # row=7, col=0 (a1) when flipped maps to display_row=0, display_col=7 (top-right)
    rect_a1_flipped = geom.cell_to_pixel(7, 0)
    assert rect_a1_flipped.x == 700
    assert rect_a1_flipped.y == 0
    assert geom.pixel_to_cell(710, 10) == (7, 0)


def test_networked_game_window_events():
    mock_client = MagicMock()
    mock_renderer = MagicMock()
    mock_geometry = BoardGeometry(8, 8)
    mock_renderer.get_geometry.return_value = mock_geometry

    window = NetworkedGameWindow.__new__(NetworkedGameWindow)
    window.network_client = mock_client
    window.renderer = mock_renderer
    window.username = "test_user"
    window.info_panel = MagicMock()
    window.view = MagicMock()
    window.board_size = 800
    window.canvas_width = 1000
    window.canvas_height = 800
    window.is_viewer = False
    window._latest_snapshot = MagicMock()
    window._pending_source = None
    window._moves = []
    window._scores = {consts.COLOR_WHITE: 0, consts.COLOR_BLACK: 0}
    window._capture_flashes = []

    # Test event_piece_moved
    window._on_event_piece_moved({
        "type": "event_piece_moved",
        "color": consts.COLOR_WHITE,
        "piece_type": "P",
        "from": "e2",
        "to": "e4",
        "at_ms": 1500,
    })

    assert len(window._moves) == 1
    assert window._moves[0].color == consts.COLOR_WHITE
    assert window._moves[0].notation == "Pe2-e4"
    assert window._moves[0].time_ms == 1500

    # Test event_score_updated
    window._on_event_score_updated({
        "type": "event_score_updated",
        "white_score": 3,
        "black_score": 1,
    })
    assert window._scores[consts.COLOR_WHITE] == 3
    assert window._scores[consts.COLOR_BLACK] == 1


def _bare_window() -> NetworkedGameWindow:
    """A NetworkedGameWindow with just enough state for message-handler tests,
    skipping the real __init__ so no Tk root window is ever created."""
    window = NetworkedGameWindow.__new__(NetworkedGameWindow)
    window._reconnect_overlay = MagicMock()
    window._disconnected_opponent_name = None
    window._on_close = MagicMock()
    return window


def test_opponent_disconnected_shows_the_countdown_with_the_opponents_name():
    window = _bare_window()

    window._on_opponent_disconnected({
        "type": "opponent_disconnected",
        "username": "Bob",
        "countdown_seconds": 30,
    })

    assert window._disconnected_opponent_name == "Bob"
    message = window._reconnect_overlay.show.call_args[0][0]
    assert "Bob" in message
    assert "30" in message


def test_countdown_tick_reuses_the_remembered_opponent_name():
    window = _bare_window()
    window._disconnected_opponent_name = "Bob"

    window._on_countdown_tick({"type": "countdown_tick", "seconds_remaining": 12})

    message = window._reconnect_overlay.show.call_args[0][0]
    assert "Bob" in message
    assert "12" in message


def test_opponent_reconnected_hides_the_overlay_and_forgets_the_name():
    window = _bare_window()
    window._disconnected_opponent_name = "Bob"

    window._on_opponent_reconnected({"type": "opponent_reconnected", "username": "Bob"})

    window._reconnect_overlay.hide.assert_called_once()
    assert window._disconnected_opponent_name is None


def test_forfeit_victory_shows_a_terminal_overlay():
    window = _bare_window()
    window._disconnected_opponent_name = "Bob"

    window._on_forfeit_victory({"type": "forfeit_victory", "reason": "opponent_disconnected_timeout"})

    window._reconnect_overlay.show_terminal.assert_called_once()
    args, kwargs = window._reconnect_overlay.show_terminal.call_args
    assert "win" in args[0].lower()
    assert kwargs["on_close"] is window._on_close
    assert window._disconnected_opponent_name is None

