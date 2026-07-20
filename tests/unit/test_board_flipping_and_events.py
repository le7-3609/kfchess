import queue
from unittest.mock import MagicMock, patch
from shared.config import consts
from shared.model.position import Position
from shared.view.game_snapshot import GameSnapshot, PieceSnapshot
from shared.view.piece_visual_state import PieceVisualState
from client.ui.rendering.board_geometry import BoardGeometry
from client.ui.rendering.pillow_renderer import PillowRenderer
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


def _single_piece_snapshot(pos: Position) -> GameSnapshot:
    piece = PieceSnapshot(
        color=consts.COLOR_BLACK,
        piece_type="P",
        has_moved=False,
        can_select=True,
        can_move=True,
        state=PieceVisualState.IDLE,
        state_elapsed_millis=0,
        state_duration_millis=0,
    )
    return GameSnapshot(
        rows=8,
        cols=8,
        pieces={pos: piece},
        selected_pos=None,
        legal_move_targets=(),
        castle_targets=(),
        active_movements=(),
        cooldown_positions=(),
        clock_ms=0,
        game_over=False,
        game_over_reason=None,
        winner=None,
    )


def test_pillow_renderer_draw_piece_flipped():
    """A flipped board must draw piece sprites at the flipped pixel cell too.

    Regression: the background, selection and legal-move highlights all routed
    through geometry.cell_to_pixel (which honors `flipped`), but _draw_piece
    computed its own pixel origin straight from the raw (row, col), leaving
    Black's sprites at the top while everything else was flipped to the bottom.
    """
    renderer = PillowRenderer("nonexistent-sprite-path")
    renderer.resize(800, 800)
    renderer.set_flipped(True)

    pos = Position(0, 0)
    snapshot = _single_piece_snapshot(pos)

    # _draw_state_effects receives the cell's top-left pixel (x, y) before the
    # sprite-centering offset, so it is the cleanest probe of where the piece
    # is anchored. Stub it out (returning 0.0 lift) and capture that origin.
    with patch.object(PillowRenderer, "_draw_state_effects", return_value=0.0) as effects:
        renderer.draw(snapshot)

    _img, _piece, x, y, _cw, _ch = effects.call_args[0]
    expected = renderer.geometry.cell_to_pixel(pos.row, pos.col)
    assert x == expected.x
    assert y == expected.y


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


def test_attach_and_run_redirects_the_running_client_instead_of_restarting_it():
    """Regression: LobbyWindow hands off a NetworkClient it already started.

    Calling `.start()` again would raise, so `attach_and_run` must instead
    redirect the client's callback onto this window's own queue and start
    this window's poll loop — otherwise game_state frames keep flowing to
    whatever queue the client was originally started with and the board
    never renders.
    """
    mock_client = MagicMock()
    window = NetworkedGameWindow.__new__(NetworkedGameWindow)
    window.network_client = mock_client
    window._message_queue = queue.Queue()
    window.root = MagicMock()

    window.attach_and_run()

    mock_client.start.assert_not_called()
    mock_client.set_message_callback.assert_called_once_with(window._message_queue.put)
    window.root.after.assert_called_once()
    window.root.mainloop.assert_called_once()


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


def test_game_end_shows_the_winners_new_rating():
    """The White winner reads its own rating change out of the "white" key,
    not the loser's — this is the parsing the client needs for the ELO sync
    to actually reach the player it applies to."""
    window = _bare_window()
    window._assigned_color = consts.COLOR_WHITE

    window._on_game_end({
        "type": "game_end",
        "reason": "checkmate",
        "winner": consts.COLOR_WHITE,
        "white": {"new_elo": 1215, "elo_change": 15},
        "black": {"new_elo": 1185, "elo_change": -15},
    })

    window._reconnect_overlay.show_terminal.assert_called_once()
    args, kwargs = window._reconnect_overlay.show_terminal.call_args
    assert "win" in args[0].lower()
    assert "1215" in args[0]
    assert "+15" in args[0]
    assert kwargs["on_close"] is window._on_close


def test_game_end_shows_the_losers_new_rating():
    window = _bare_window()
    window._assigned_color = consts.COLOR_BLACK

    window._on_game_end({
        "type": "game_end",
        "reason": "checkmate",
        "winner": consts.COLOR_WHITE,
        "white": {"new_elo": 1215, "elo_change": 15},
        "black": {"new_elo": 1185, "elo_change": -15},
    })

    args, _ = window._reconnect_overlay.show_terminal.call_args
    assert "lose" in args[0].lower()
    assert "1185" in args[0]
    assert "-15" in args[0]


def test_game_end_draw_with_no_rating_payload_shows_result_only():
    """An unrated game (no database, or a bot involved) omits both rating
    keys entirely — the handler must not choke looking one up."""
    window = _bare_window()
    window._assigned_color = consts.COLOR_WHITE

    window._on_game_end({"type": "game_end", "reason": "stalemate", "winner": None})

    args, _ = window._reconnect_overlay.show_terminal.call_args
    assert "draw" in args[0].lower()
    assert "rating" not in args[0].lower()


def test_game_end_after_forfeit_credits_the_win_and_the_rating():
    """A rated forfeit's game_end frame supersedes the plain forfeit_victory
    text with a message that names the reason and the rating change."""
    window = _bare_window()
    window._assigned_color = consts.COLOR_BLACK

    window._on_game_end({
        "type": "game_end",
        "reason": "disconnection_timeout",
        "winner": consts.COLOR_BLACK,
        "white": {"new_elo": 1185, "elo_change": -15},
        "black": {"new_elo": 1215, "elo_change": 15},
    })

    args, _ = window._reconnect_overlay.show_terminal.call_args
    assert "forfeited" in args[0].lower()
    assert "win" in args[0].lower()
    assert "1215" in args[0]


def test_tk_image_view_uses_canvas_as_master():
    from client.ui.window.image_view import TkImageView
    canvas = MagicMock()
    canvas_image_id = 1
    image_mock = MagicMock()
    pil_img_mock = MagicMock()
    image_mock.get.return_value = pil_img_mock

    view = TkImageView(canvas, canvas_image_id)
    with MagicMock() as mock_photo_image:
        from unittest.mock import patch
        with patch("client.ui.window.image_view.ImageTk.PhotoImage", return_value=mock_photo_image) as mock_photo_cls:
            view.show(image_mock)
            mock_photo_cls.assert_called_once_with(pil_img_mock, master=canvas)
            canvas.itemconfig.assert_called_once_with(canvas_image_id, image=mock_photo_image)


