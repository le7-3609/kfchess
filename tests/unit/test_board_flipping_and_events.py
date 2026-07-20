"""Board orientation and the tkinter image view.

The message-handling that used to live in this file moved out with the wire
protocol itself: see tests/unit/test_network_game_controller.py for frame
decoding and tests/unit/test_game_window.py for what the window does with the
typed callbacks it gets back.
"""

from unittest.mock import MagicMock, patch

from shared.config import consts
from shared.model.position import Position
from shared.view.game_snapshot import GameSnapshot, PieceSnapshot
from shared.view.piece_visual_state import PieceVisualState
from client.ui.rendering.board_geometry import BoardGeometry
from client.ui.rendering.pillow_renderer import PillowRenderer


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


def test_tk_image_view_uses_canvas_as_master():
    from client.ui.window.image_view import TkImageView

    canvas = MagicMock()
    canvas_image_id = 1
    image_mock = MagicMock()
    pil_img_mock = MagicMock()
    image_mock.get.return_value = pil_img_mock

    mock_photo_image = MagicMock()
    view = TkImageView(canvas, canvas_image_id)
    with patch(
        "client.ui.window.image_view.ImageTk.PhotoImage", return_value=mock_photo_image
    ) as mock_photo_cls:
        view.show(image_mock)
        mock_photo_cls.assert_called_once_with(pil_img_mock, master=canvas)
        canvas.itemconfig.assert_called_once_with(canvas_image_id, image=mock_photo_image)
