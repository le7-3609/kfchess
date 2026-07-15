"""Pillow-backed board renderer — concrete RendererInterface (Layer 6).

Owns: painting a GameSnapshot into an Img (checkerboard, pieces, selection,
legal-move/castle highlights, jump/cooldown visuals, game-over banner).
Must not own: game rules, board mutation, input parsing, or text-test logic
— everything drawn here comes from the read-only GameSnapshot.
"""

import math

from kungfu_chess.model.position import Position
from kungfu_chess.view.board_geometry import BoardGeometry
from kungfu_chess.view.game_snapshot import GameSnapshot, PieceSnapshot
from kungfu_chess.view.img import Img
from kungfu_chess.view.piece_visual_state import PieceVisualState
from kungfu_chess.view.renderer import RendererInterface
from kungfu_chess.view.sprite_library import SpriteLibrary

LIGHT_SQUARE = (240, 217, 181, 255)
DARK_SQUARE = (181, 136, 99, 255)
SELECTION_COLOR = (220, 30, 30, 255)
LEGAL_MOVE_CAPTURE_COLOR = (160, 70, 200, 145)
LEGAL_MOVE_EMPTY_COLOR = (70, 140, 220, 145)
CASTLE_TARGET_COLOR = (240, 200, 40, 160)
GAME_OVER_OVERLAY_COLOR = (0, 0, 0, 160)
GAME_OVER_TEXT_COLOR = (255, 255, 255, 255)

REST_RED = (219, 68, 55)
REST_AMBER = (240, 173, 40)
REST_GREEN = (52, 168, 83)

_GAME_OVER_LABELS = {
    "king_captured": "KING CAPTURED",
    "checkmate": "CHECKMATE",
    "stalemate": "STALEMATE",
    "insufficient_material": "DRAW - INSUFFICIENT MATERIAL",
    "threefold_repetition": "DRAW - THREEFOLD REPETITION",
    "fifty_move_rule": "DRAW - FIFTY MOVE RULE",
}

_COLOR_NAMES = {"w": "WHITE", "b": "BLACK"}


def _rest_color(remaining_fraction: float) -> tuple[int, int, int, int]:
    remaining_fraction = min(1.0, max(0.0, remaining_fraction))
    if remaining_fraction >= 0.5:
        t = (remaining_fraction - 0.5) / 0.5
        c = _lerp_color(REST_AMBER, REST_GREEN, t)
    else:
        t = remaining_fraction / 0.5
        c = _lerp_color(REST_RED, REST_AMBER, t)
    return (*c, 255)


def _lerp_color(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _game_over_text(snapshot: GameSnapshot) -> str:
    label = _GAME_OVER_LABELS.get(snapshot.game_over_reason, "GAME OVER")
    if snapshot.winner is not None:
        return f"{label} - {_COLOR_NAMES.get(snapshot.winner, snapshot.winner)} WINS"
    return f"{label} - DRAW"


class PillowRenderer(RendererInterface):
    """Draws a GameSnapshot into an in-memory Img using SpriteLibrary art."""

    def __init__(self, sprite_base_path: str):
        self.geometry: BoardGeometry | None = None
        self.sprites = SpriteLibrary(sprite_base_path)
        self._image: Img | None = None
        self._width = 0
        self._height = 0

    def resize(self, width: int, height: int) -> None:
        self._width = width
        self._height = height

    def reload_sprites(self, sprite_base_path: str) -> None:
        """Swap in a different piece-art theme without rebuilding the renderer."""
        self.sprites = SpriteLibrary(sprite_base_path)

    def get_geometry(self) -> BoardGeometry:
        assert self.geometry is not None, "draw() must be called at least once before get_geometry()"
        return self.geometry

    def get_image(self) -> Img:
        assert self._image is not None, "draw() must be called before get_image()"
        return self._image

    # -- RendererInterface ----------------------------------------------

    def draw(self, snapshot: GameSnapshot) -> None:
        if self.geometry is None or self.geometry.rows != snapshot.rows or self.geometry.cols != snapshot.cols:
            self.geometry = BoardGeometry(snapshot.rows, snapshot.cols)
        self.geometry.resize(self._width, self._height)

        img = Img().blank(self._width, self._height, (30, 30, 30, 255))

        self._draw_checkerboard(img, snapshot)
        self._draw_legal_moves(img, snapshot)
        self._draw_castle_targets(img, snapshot)
        self._draw_selection(img, snapshot)
        for pos, piece in snapshot.pieces.items():
            self._draw_piece(img, pos, piece, snapshot)
        if snapshot.game_over:
            self._draw_game_over(img, snapshot)

        self._image = img

    # -- drawing steps ----------------------------------------------------

    def _draw_checkerboard(self, img: Img, snapshot: GameSnapshot) -> None:
        for r in range(snapshot.rows):
            for c in range(snapshot.cols):
                rect = self.geometry.cell_to_pixel(r, c)
                color = LIGHT_SQUARE if (r + c) % 2 == 0 else DARK_SQUARE
                img.fill_rect(rect.x, rect.y, rect.width, rect.height, color)

    def _draw_selection(self, img: Img, snapshot: GameSnapshot) -> None:
        pos = snapshot.selected_pos
        if pos is None:
            return
        rect = self.geometry.cell_to_pixel(pos.row, pos.col)
        inset = 2
        img.draw_rect(
            rect.x + inset, rect.y + inset, rect.width - 2 * inset, rect.height - 2 * inset,
            SELECTION_COLOR, width=4,
        )

    def _draw_legal_moves(self, img: Img, snapshot: GameSnapshot) -> None:
        for pos in snapshot.legal_move_targets:
            rect = self.geometry.cell_to_pixel(pos.row, pos.col)
            occupied = snapshot.piece_at(pos) is not None
            color = LEGAL_MOVE_CAPTURE_COLOR if occupied else LEGAL_MOVE_EMPTY_COLOR
            img.fill_rect(rect.x, rect.y, rect.width, rect.height, color)

    def _draw_castle_targets(self, img: Img, snapshot: GameSnapshot) -> None:
        for pos in snapshot.castle_targets:
            rect = self.geometry.cell_to_pixel(pos.row, pos.col)
            img.fill_rect(rect.x, rect.y, rect.width, rect.height, CASTLE_TARGET_COLOR)

    def _draw_piece(self, img: Img, pos: Position, piece: PieceSnapshot, snapshot: GameSnapshot) -> None:
        movement = self._movement_for(pos, snapshot)

        cell_w = self.geometry.get_cell_width()
        cell_h = self.geometry.get_cell_height()

        if movement is not None:
            col, row = self._interpolated_cell(movement, snapshot.clock_ms)
        else:
            col, row = pos.col, pos.row

        x = self.geometry.origin_x + col * cell_w
        y = self.geometry.origin_y + row * cell_h

        size = round(min(cell_w, cell_h) * 0.85)
        center_x = x + cell_w / 2
        center_y = y + cell_h / 2

        draw_x = center_x - size / 2
        draw_y = center_y - size / 2

        if piece.state == PieceVisualState.JUMP:
            t = min(1.0, max(0.0, piece.state_elapsed_millis / piece.state_duration_millis)) \
                if piece.state_duration_millis else 0.0
            shadow_w = cell_w * 0.7
            shadow_h = cell_h * 0.18
            shadow_x = center_x - shadow_w / 2
            shadow_y = y + cell_h * 0.82 - shadow_h / 2
            img.fill_ellipse(round(shadow_x), round(shadow_y), round(shadow_w), round(shadow_h), (0, 0, 0, 90))

            bounce_height = cell_h * 0.4
            draw_y -= bounce_height * math.sin(math.pi * t)

        elif piece.state == PieceVisualState.SHORT_REST and piece.state_duration_millis > 0:
            remaining_fraction = 1.0 - min(1.0, max(0.0, piece.state_elapsed_millis / piece.state_duration_millis))
            self._draw_rest_square(img, x, y, cell_w, cell_h, remaining_fraction)

        frame = self.sprites.frame_for(piece.piece_type, piece.color, piece.state, piece.state_elapsed_millis)
        if frame is not None and size > 0:
            sprite_img = Img().from_pil(frame.resize((size, size)))
            sprite_img.draw_on(img, round(draw_x), round(draw_y))

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
        img.fill_rect(round(x), round(y + cell_h - fill_height), round(cell_w), fill_height, (*color[:3], 110))

    def _draw_game_over(self, img: Img, snapshot: GameSnapshot) -> None:
        img.fill_rect(0, 0, img.get().width, img.get().height, GAME_OVER_OVERLAY_COLOR)
        font_size = max(16, self.geometry.board_size / 16)
        img.put_text(
            _game_over_text(snapshot), img.get().width // 2, img.get().height // 2,
            font_size, GAME_OVER_TEXT_COLOR, anchor="mm",
        )
