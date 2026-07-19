"""Pillow-backed board renderer — concrete RendererInterface (Layer 6).

Owns: painting a GameSnapshot into an Img (checkerboard, pieces, selection,
legal-move/castle highlights, jump/cooldown visuals, game-over banner).
Must not own: game rules, board mutation, input parsing, or text-test logic
— everything drawn here comes from the read-only GameSnapshot.
"""

import math

from kungfu_chess.ui import consts as ui_consts
from kungfu_chess.ui.rendering.board_geometry import BoardGeometry
from kungfu_chess.ui.rendering.img import Img
from kungfu_chess.ui.rendering.sprite_library import SpriteLibrary
from kungfu_chess.model.position import Position
from kungfu_chess.view.game_snapshot import GameSnapshot, PieceSnapshot
from kungfu_chess.view.piece_visual_state import PieceVisualState
from kungfu_chess.view.renderer import RendererInterface

# Re-exported so callers can name the highlight colors without depending on
# the constant registry directly.
LEGAL_MOVE_CAPTURE_COLOR = ui_consts.LEGAL_MOVE_CAPTURE_COLOR
LEGAL_MOVE_EMPTY_COLOR = ui_consts.LEGAL_MOVE_EMPTY_COLOR


def _rest_color(remaining_fraction: float) -> tuple[int, int, int, int]:
    """Blend red -> amber -> green as a cooldown drains, for the rest indicator."""
    remaining_fraction = min(1.0, max(0.0, remaining_fraction))
    midpoint = ui_consts.REST_MIDPOINT_FRACTION
    if remaining_fraction >= midpoint:
        t = (remaining_fraction - midpoint) / midpoint
        c = _lerp_color(ui_consts.REST_AMBER, ui_consts.REST_GREEN, t)
    else:
        t = remaining_fraction / midpoint
        c = _lerp_color(ui_consts.REST_RED, ui_consts.REST_AMBER, t)
    return (*c, ui_consts.COLOR_CHANNEL_MAX)


def _lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(
        round(a[i] + (b[i] - a[i]) * t) for i in range(ui_consts.RGB_CHANNEL_COUNT)
    )


def _game_over_text(snapshot: GameSnapshot) -> str:
    label = ui_consts.GAME_OVER_LABELS.get(
        snapshot.game_over_reason, ui_consts.GAME_OVER_DEFAULT_LABEL
    )
    if snapshot.winner is not None:
        winner_name = ui_consts.COLOR_BANNER_NAMES.get(snapshot.winner, snapshot.winner)
        return f"{label} - {winner_name} {ui_consts.GAME_OVER_WINNER_SUFFIX}"
    return f"{label} - {ui_consts.GAME_OVER_DRAW_SUFFIX}"


class PillowRenderer(RendererInterface):
    """Draws a GameSnapshot into an in-memory Img using SpriteLibrary art."""

    def __init__(self, sprite_base_path: str):
        self.geometry: BoardGeometry | None = None
        self.sprites = SpriteLibrary(sprite_base_path)
        self._image: Img | None = None
        self._width = 0
        self._height = 0
        self._background_key: tuple | None = None
        self._background_img: Img | None = None
        self._light_square = ui_consts.LIGHT_SQUARE
        self._dark_square = ui_consts.DARK_SQUARE

    def resize(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

    def reload_sprites(self, sprite_base_path: str) -> None:
        """Swap in a different piece-art theme without rebuilding the renderer."""
        self.sprites = SpriteLibrary(sprite_base_path)

    def set_board_theme(self, light_color: tuple, dark_color: tuple) -> None:
        """Swap in a different board color theme without rebuilding the renderer."""
        self._light_square = light_color
        self._dark_square = dark_color
        self._background_key = None
        self._background_img = None

    def get_geometry(self) -> BoardGeometry:
        assert self.geometry is not None, "draw() must be called at least once before get_geometry()"
        return self.geometry

    def get_image(self) -> Img:
        assert self._image is not None, "draw() must be called before get_image()"
        return self._image

    def draw(self, snapshot: GameSnapshot) -> None:
        if self.geometry is None or self.geometry.rows != snapshot.rows or self.geometry.cols != snapshot.cols:
            self.geometry = BoardGeometry(snapshot.rows, snapshot.cols)
        self.geometry.resize(self._width, self._height)

        img = self._background(snapshot)

        self._draw_castle_targets(img, snapshot)
        self._draw_selection(img, snapshot)
        for pos, piece in snapshot.pieces.items():
            self._draw_piece(img, pos, piece, snapshot)
        self._draw_legal_moves(img, snapshot)
        if snapshot.game_over:
            self._draw_game_over(img, snapshot)

        self._image = img

    def _background(self, snapshot: GameSnapshot) -> Img:
        """Return a fresh copy of the blank-board+checkerboard base image.

        The checkerboard never changes shape or color between frames for a
        fixed board size, so it's built once per (size, rows, cols) and
        reused via a cheap raw-buffer .copy() instead of reissuing 64
        ImageDraw.rectangle calls (plus a fresh Image.new) on every 16ms
        render tick.
        """
        key = (self._width, self._height, snapshot.rows, snapshot.cols)
        if self._background_img is None or self._background_key != key:
            base = Img().blank(self._width, self._height, ui_consts.BOARD_BACKDROP_COLOR)
            for r in range(snapshot.rows):
                for c in range(snapshot.cols):
                    rect = self.geometry.cell_to_pixel(r, c)
                    is_light = (r + c) % ui_consts.CHECKERBOARD_MODULUS == 0
                    color = self._light_square if is_light else self._dark_square
                    base.fill_rect(rect.x, rect.y, rect.width, rect.height, color)
            self._background_img = base
            self._background_key = key
        return Img().from_pil(self._background_img.get())

    def _draw_selection(self, img: Img, snapshot: GameSnapshot) -> None:
        pos = snapshot.selected_pos
        if pos is None:
            return
        rect = self.geometry.cell_to_pixel(pos.row, pos.col)
        inset = ui_consts.SELECTION_INSET_PX
        img.draw_rect(
            rect.x + inset, rect.y + inset, rect.width - 2 * inset, rect.height - 2 * inset,
            ui_consts.SELECTION_COLOR, width=ui_consts.SELECTION_BORDER_WIDTH,
        )

    def _draw_legal_moves(self, img: Img, snapshot: GameSnapshot) -> None:
        for pos in snapshot.legal_move_targets:
            rect = self.geometry.cell_to_pixel(pos.row, pos.col)
            occupied = snapshot.piece_at(pos) is not None
            color = (
                ui_consts.LEGAL_MOVE_CAPTURE_COLOR if occupied else ui_consts.LEGAL_MOVE_EMPTY_COLOR
            )
            cell_w = rect.width
            cell_h = rect.height
            cx = rect.x + cell_w / 2
            cy = rect.y + cell_h / 2
            r = min(cell_w, cell_h) * ui_consts.LEGAL_MOVE_DOT_RADIUS_RATIO
            img.fill_ellipse(round(cx - r), round(cy - r), round(2 * r), round(2 * r), color)

    def _draw_castle_targets(self, img: Img, snapshot: GameSnapshot) -> None:
        for pos in snapshot.castle_targets:
            rect = self.geometry.cell_to_pixel(pos.row, pos.col)
            img.fill_rect(rect.x, rect.y, rect.width, rect.height, ui_consts.CASTLE_TARGET_COLOR)

    def _draw_piece(self, img: Img, pos: Position, piece: PieceSnapshot, snapshot: GameSnapshot) -> None:
        """Draw *piece* onto *img*, sliding it along its path if it is mid-move."""
        cell_w = self.geometry.get_cell_width()
        cell_h = self.geometry.get_cell_height()
        col, row = self._current_cell(pos, snapshot)

        x = self.geometry.origin_x + col * cell_w
        y = self.geometry.origin_y + row * cell_h
        size = round(min(cell_w, cell_h) * ui_consts.PIECE_SPRITE_SIZE_RATIO)

        lift = self._draw_state_effects(img, piece, x, y, cell_w, cell_h)
        draw_x = x + (cell_w - size) / 2
        draw_y = y + (cell_h - size) / 2 - lift

        self._blit_sprite(img, piece, size, draw_x, draw_y)

    def _current_cell(self, pos: Position, snapshot: GameSnapshot):
        """Return the (col, row) to draw at: interpolated while in flight, else *pos*."""
        movement = self._movement_for(pos, snapshot)
        if movement is None:
            return pos.col, pos.row
        return self._interpolated_cell(movement, snapshot.clock_ms)

    def _draw_state_effects(
        self, img: Img, piece: PieceSnapshot, x: float, y: float, cell_w: float, cell_h: float
    ) -> float:
        """Draw the overlay for *piece*'s visual state and return its vertical lift.

        Only a jumping piece leaves the ground, so every other state lifts by 0.
        """
        if piece.state == PieceVisualState.JUMP:
            return self._draw_jump(img, piece, x, y, cell_w, cell_h)
        if piece.state == PieceVisualState.SHORT_REST and piece.state_duration_millis > 0:
            self._draw_rest_square(img, x, y, cell_w, cell_h, 1.0 - self._state_progress(piece))
        return 0.0

    def _draw_jump(
        self, img: Img, piece: PieceSnapshot, x: float, y: float, cell_w: float, cell_h: float
    ) -> float:
        """Draw the ground shadow of a jumping piece and return how far it has risen."""
        shadow_w = cell_w * ui_consts.JUMP_SHADOW_WIDTH_RATIO
        shadow_h = cell_h * ui_consts.JUMP_SHADOW_HEIGHT_RATIO
        shadow_x = x + (cell_w - shadow_w) / 2
        shadow_y = y + cell_h * ui_consts.JUMP_SHADOW_Y_RATIO - shadow_h / 2
        img.fill_ellipse(
            round(shadow_x), round(shadow_y), round(shadow_w), round(shadow_h),
            ui_consts.JUMP_SHADOW_COLOR,
        )
        # A half sine peaks mid-jump and returns to 0 on landing.
        return cell_h * ui_consts.JUMP_LIFT_RATIO * math.sin(math.pi * self._state_progress(piece))

    def _state_progress(self, piece: PieceSnapshot) -> float:
        """How far *piece* is through its current visual state, clamped to 0.0-1.0."""
        if not piece.state_duration_millis:
            return 0.0
        return min(1.0, max(0.0, piece.state_elapsed_millis / piece.state_duration_millis))

    def _blit_sprite(self, img: Img, piece: PieceSnapshot, size: int, draw_x: float, draw_y: float) -> None:
        """Composite *piece*'s current sprite frame onto *img*, if one is available."""
        frame = self.sprites.sized_frame_for(
            piece.piece_type, piece.color, piece.state, piece.state_elapsed_millis, size
        )
        if frame is not None:
            Img().from_pil(frame).draw_on(img, round(draw_x), round(draw_y))

    def _movement_for(self, pos: Position, snapshot: GameSnapshot):
        for movement in snapshot.active_movements:
            if movement.frm == pos:
                return movement
        return None

    def _interpolated_cell(self, movement, clock_ms: int) -> tuple[float, float]:
        total = movement.arrival_ms - movement.start_ms
        t = 1.0 if total <= 0 else (clock_ms - movement.start_ms) / total
        t = min(1.0, max(0.0, t))
        if movement.frm == movement.to:
            return movement.frm.col, movement.frm.row
        col = movement.frm.col + (movement.to.col - movement.frm.col) * t
        row = movement.frm.row + (movement.to.row - movement.frm.row) * t
        return col, row

    def _draw_rest_square(self, img: Img, x: float, y: float, cell_w: float, cell_h: float, remaining_fraction: float) -> None:
        fill_height = round(cell_h * remaining_fraction)
        color = _rest_color(remaining_fraction)
        rgb = color[:ui_consts.RGB_CHANNEL_COUNT]
        img.fill_rect(
            round(x), round(y + cell_h - fill_height), round(cell_w), fill_height,
            (*rgb, ui_consts.REST_FILL_ALPHA),
        )

    def _draw_game_over(self, img: Img, snapshot: GameSnapshot) -> None:
        img.fill_rect(0, 0, img.get().width, img.get().height, ui_consts.GAME_OVER_OVERLAY_COLOR)
        text = _game_over_text(snapshot)
        img.put_text(
            text, img.get().width // 2, img.get().height // 2,
            self._game_over_font_size(img, text), ui_consts.GAME_OVER_TEXT_COLOR,
            anchor=ui_consts.TEXT_ANCHOR_MIDDLE_MIDDLE,
        )

    def _game_over_font_size(self, img: Img, text: str) -> float:
        """Size the banner so it fills the board without overflowing its width."""
        width_budget = img.get().width * ui_consts.GAME_OVER_TEXT_WIDTH_FRACTION
        estimated_glyph_width = max(1, len(text) * ui_consts.GAME_OVER_FONT_WIDTH_RATIO)
        width_limited = width_budget / estimated_glyph_width
        board_relative = self.geometry.board_size / ui_consts.GAME_OVER_FONT_SIZE_DIVISOR
        return min(max(ui_consts.GAME_OVER_MIN_FONT_SIZE, board_relative), width_limited)
