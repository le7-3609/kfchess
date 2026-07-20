"""GameWindow: what the one window does with the typed callbacks it receives.

Every window here is built with `__new__` so no real Tk root is ever created.
The point of the class is that it behaves identically whichever
IGameController it holds, so these drive it through the seam rather than
through either mode's transport.
"""

from unittest.mock import MagicMock

import pytest

from client.ui import consts as ui_consts
from shared.config import consts
from shared.io.moves_log import MoveLogEntry
from shared.model.position import Position
from shared.view.game_snapshot import GameSnapshot
from client.game_controller import GameNotice, GameSessionInfo, NoticeLevel
from client.ui.rendering.board_geometry import BoardGeometry
from client.ui.window.game_window import GameWindow


_BOARD_SIZE = 800
_CANVAS_WIDTH = 1000
_CANVAS_HEIGHT = 800
_BOARD_X_OFFSET = ui_consts.SIDE_PANEL_WIDTH + (
    _CANVAS_WIDTH - ui_consts.SIDE_PANEL_WIDTH * 2 - _BOARD_SIZE
) // 2
_BOARD_Y_OFFSET = ui_consts.PANEL_TOP_HEIGHT + (
    _CANVAS_HEIGHT - ui_consts.PANEL_TOP_HEIGHT - _BOARD_SIZE
) // 2


def _empty_snapshot() -> GameSnapshot:
    """A real snapshot, because `_refresh` rewrites it with `dataclasses.replace`."""
    return GameSnapshot(
        rows=8,
        cols=8,
        pieces={},
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


def _geometry() -> BoardGeometry:
    """A geometry sized like the window sizes its own, so clicks map to cells."""
    geometry = BoardGeometry(8, 8)
    geometry.resize(_BOARD_SIZE, _BOARD_SIZE)
    return geometry


def _window(controller=None) -> GameWindow:
    """A GameWindow with just enough state for listener/gesture tests."""
    window = GameWindow.__new__(GameWindow)
    if controller is None:
        controller = MagicMock()
        controller.assigned_color = None
    window.controller = controller
    window.username = "tester"
    window.renderer = MagicMock()
    window.renderer.get_geometry.return_value = _geometry()
    window.info_panel = MagicMock()
    window.view = MagicMock()
    window.root = MagicMock()
    window._overlay = MagicMock()
    window.board_size = 800
    window.canvas_width = 1000
    window.canvas_height = 800
    window._latest_snapshot = _empty_snapshot()
    window._pending_source = None
    window._moves = []
    window._scores = {consts.COLOR_WHITE: 0, consts.COLOR_BLACK: 0}
    window._capture_flashes = []
    return window


def _click_at(row: int, col: int):
    """A click event landing in the middle of board cell (row, col).

    The window offsets by the side panel and top strip before mapping, so
    those are added back here.
    """
    rect = _geometry().cell_to_pixel(row, col)
    event = MagicMock()
    event.x = _BOARD_X_OFFSET + rect.x + rect.width // 2
    event.y = _BOARD_Y_OFFSET + rect.y + rect.height // 2
    return event


def test_run_starts_the_controller_and_begins_polling():
    controller = MagicMock()
    controller.poll_interval_ms = 20
    window = _window(controller)

    window.run()

    controller.start.assert_called_once_with(window)
    window.root.after.assert_called_once()
    window.root.mainloop.assert_called_once()


def test_closing_leaves_the_match_and_destroys_the_window():
    controller = MagicMock()
    window = _window(controller)

    window.close()

    controller.leave.assert_called_once()
    window.root.destroy.assert_called_once()


def test_a_black_seat_flips_the_board_and_names_both_panels():
    window = _window()

    window.on_session_started(
        GameSessionInfo(assigned_color=consts.COLOR_BLACK, opponent_name="Alice")
    )

    window.renderer.set_flipped.assert_called_once_with(True)
    assert window.info_panel.white_name == "Alice"
    assert window.info_panel.black_name == "tester"


def test_an_unowned_offline_session_leaves_the_board_unflipped():
    """Two players sharing a machine hold no seat between them, so neither
    orientation is "theirs" and the panels fall back to the color names."""
    window = _window()

    window.on_session_started(GameSessionInfo(assigned_color=None, opponent_name="Local opponent"))

    window.renderer.set_flipped.assert_not_called()
    assert window.info_panel.white_name == "White"
    assert window.info_panel.black_name == "Black"


def test_recorded_moves_and_scores_accumulate_for_the_next_render():
    window = _window()

    window.on_move_recorded(MoveLogEntry(color=consts.COLOR_WHITE, notation="Pe2-e4", time_ms=1))
    window.on_score_changed(3, 1)
    window.on_capture(Position(4, 4), 900)

    assert [entry.notation for entry in window._moves] == ["Pe2-e4"]
    assert window._scores == {consts.COLOR_WHITE: 3, consts.COLOR_BLACK: 1}
    assert window._capture_flashes[0].pos == Position(4, 4)


@pytest.mark.parametrize(
    "level, expected_call",
    [
        (NoticeLevel.TRANSIENT, "show"),
        (NoticeLevel.TERMINAL, "show_terminal"),
        (NoticeLevel.CLEARED, "hide"),
    ],
)
def test_each_notice_level_drives_its_own_overlay_action(level, expected_call):
    window = _window()

    window.on_notice(GameNotice(level, "something happened"))

    assert getattr(window._overlay, expected_call).called


def test_the_first_click_selects_and_tells_the_controller_about_it():
    """The window holds the highlight itself, but a locally-simulated match
    needs the selection to answer with legal moves."""
    controller = MagicMock()
    controller.is_viewer = False
    window = _window(controller)

    window._on_left_click(_click_at(6, 4))

    assert window._pending_source == (6, 4)
    controller.submit_select.assert_called_once_with(Position(6, 4))
    controller.submit_move.assert_not_called()


def test_the_second_click_submits_the_whole_move_and_clears_the_selection():
    controller = MagicMock()
    controller.is_viewer = False
    window = _window(controller)

    window._on_left_click(_click_at(6, 4))
    window._on_left_click(_click_at(4, 4))

    controller.submit_move.assert_called_once_with(Position(6, 4), Position(4, 4))
    assert window._pending_source is None


def test_clicking_the_same_square_twice_submits_nothing():
    controller = MagicMock()
    controller.is_viewer = False
    window = _window(controller)

    window._on_left_click(_click_at(6, 4))
    window._on_left_click(_click_at(6, 4))

    controller.submit_move.assert_not_called()
    assert window._pending_source is None


def test_a_spectator_cannot_move():
    controller = MagicMock()
    controller.is_viewer = True
    window = _window(controller)

    window._on_left_click(_click_at(6, 4))

    assert window._pending_source is None
    controller.submit_select.assert_not_called()
    controller.submit_move.assert_not_called()


def test_right_click_asks_the_controller_to_jump_in_place():
    controller = MagicMock()
    controller.is_viewer = False
    window = _window(controller)

    window._on_right_click(_click_at(6, 4))

    controller.submit_jump.assert_called_once_with(Position(6, 4))


def test_cannot_select_or_jump_opponent_piece_when_assigned_color_set():
    from shared.view.piece_visual_state import PieceVisualState
    from shared.view.game_snapshot import PieceSnapshot, GameSnapshot

    controller = MagicMock()
    controller.is_viewer = False
    controller.assigned_color = consts.COLOR_WHITE
    window = _window(controller)

    black_pawn = PieceSnapshot(
        color=consts.COLOR_BLACK,
        piece_type="pawn",
        has_moved=False,
        can_select=True,
        can_move=True,
        state=PieceVisualState.IDLE,
        state_elapsed_millis=0,
        state_duration_millis=0,
    )
    window._latest_snapshot = GameSnapshot(
        rows=8,
        cols=8,
        pieces={Position(1, 4): black_pawn},
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

    # First left click on Black pawn should be ignored
    window._on_left_click(_click_at(1, 4))
    assert window._pending_source is None
    controller.submit_select.assert_not_called()

    # Right click on Black pawn should be ignored
    window._on_right_click(_click_at(1, 4))
    controller.submit_jump.assert_not_called()
