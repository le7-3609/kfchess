"""Sprite library — loads and animates per-piece sprite sheets (Layer 6).

Owns: reading ui/assets/colors/<code>/states/<state>/{config.json,sprites/*.png}
from disk and picking the correct animation frame for a piece's current
visual state and elapsed time.
Must not own: game rules, board mutation, input parsing, or timing
advancement (elapsed_millis is handed in, never measured here).
"""

import os
import re

from PIL import Image, ImageFilter

from kungfu_chess.config import consts
from kungfu_chess.ui import consts as ui_consts
from kungfu_chess.view.piece_visual_state import PieceVisualState

_FPS_PATTERN = re.compile(ui_consts.SPRITE_FPS_PATTERN)
_LOOP_PATTERN = re.compile(ui_consts.SPRITE_LOOP_PATTERN)

# Keyed by the view-layer enum, so this mapping stays here rather than in the
# constant registry, which must not import from an outer layer.
_STATE_NAMES = {
    PieceVisualState.IDLE: "idle",
    PieceVisualState.MOVE: "move",
    PieceVisualState.JUMP: "jump",
    PieceVisualState.SHORT_REST: "short_rest",
    PieceVisualState.LONG_REST: "long_rest",
}

class _Animation:
    def __init__(self, frames: list[Image.Image], frames_per_sec: int, is_loop: bool):
        self.frames = frames
        self.frames_per_sec = frames_per_sec
        self.is_loop = is_loop


def _folder_name_candidates(piece_type: str, color: str) -> list[str]:
    """Different asset sets use different folder-naming conventions, e.g.
    "KW"/"KB" (piece letter + uppercase color) vs "wK"/"bK" (lowercase color
    + piece letter). Try both so any theme folder can be dropped in as-is."""
    return [f"{piece_type}{color.upper()}", f"{color}{piece_type}"]


def _to_black_and_white(src: Image.Image, dark: bool) -> Image.Image:
    """Remap every pixel's luminance into a narrow band so pieces render as strict
    black/white silhouettes regardless of the source sprite sheet's actual palette,
    preserving the original alpha channel.

    Also composites a thin contrasting outline behind each piece so it is
    visible against any board-square color:
    - white pieces  → black/dark shadow
    - black pieces  → white glow
    """
    src = src.convert(ui_consts.IMAGE_MODE_RGBA)
    range_low = (
        ui_consts.SPRITE_DARK_LUMINANCE_FLOOR if dark else ui_consts.SPRITE_LIGHT_LUMINANCE_FLOOR
    )
    channel_max = ui_consts.COLOR_CHANNEL_MAX

    lut = [
        max(
            ui_consts.COLOR_CHANNEL_MIN,
            min(channel_max, range_low + round((lum / channel_max) * ui_consts.SPRITE_LUMINANCE_SPAN)),
        )
        for lum in range(ui_consts.COLOR_CHANNEL_LEVELS)
    ]

    luminance = src.convert(ui_consts.IMAGE_MODE_RGB).convert(
        ui_consts.IMAGE_MODE_LUMINANCE, matrix=ui_consts.LUMINANCE_MATRIX
    )
    remapped = luminance.point(lut)
    alpha = src.getchannel(ui_consts.IMAGE_CHANNEL_ALPHA)

    piece = Image.merge(ui_consts.IMAGE_MODE_RGBA, (remapped, remapped, remapped, alpha))

    # Blur the alpha channel into a soft halo slightly larger than the piece,
    # colored opposite the piece so it reads against any board-square color.
    halo_alpha = alpha.filter(ImageFilter.GaussianBlur(radius=ui_consts.SPRITE_OUTLINE_BLUR_RADIUS))
    outline_value = ui_consts.COLOR_CHANNEL_MAX if dark else ui_consts.COLOR_CHANNEL_MIN
    outline_layer = Image.new(
        ui_consts.IMAGE_MODE_RGBA, piece.size, (outline_value, outline_value, outline_value, 0)
    )
    outline_layer.putalpha(halo_alpha)

    result = Image.new(ui_consts.IMAGE_MODE_RGBA, piece.size, ui_consts.SPRITE_TRANSPARENT_RGBA)
    result = Image.alpha_composite(result, outline_layer)
    result = Image.alpha_composite(result, piece)
    return result


class SpriteLibrary:
    def __init__(self, base_path: str):
        self._base_path = base_path
        self._animations: dict[tuple[str, str, PieceVisualState], _Animation] = {}
        self._resize_cache: dict[tuple[int, int], Image.Image] = {}

        for piece_type in consts.ALL_PIECE_TYPES:
            for color in consts.ALL_COLORS:
                for state in PieceVisualState:
                    self._load(piece_type, color, state)

    def _load(self, piece_type: str, color: str, state: PieceVisualState) -> None:
        candidates = _folder_name_candidates(piece_type, color)
        state_dir = None
        for folder in candidates:
            candidate = os.path.join(
                self._base_path, folder, ui_consts.SPRITE_STATES_DIR, _STATE_NAMES[state]
            )
            if os.path.isdir(candidate):
                state_dir = candidate
                break
        if state_dir is None:
            state_dir = os.path.join(
                self._base_path, candidates[0], ui_consts.SPRITE_STATES_DIR, _STATE_NAMES[state]
            )

        frames_per_sec, is_loop = self._read_config(state_dir)
        frames = self._read_frames(state_dir, dark=(color == consts.COLOR_BLACK))

        self._animations[(piece_type, color, state)] = _Animation(frames, frames_per_sec, is_loop)

    @staticmethod
    def _read_config(state_dir: str) -> tuple[int, bool]:
        config_path = os.path.join(state_dir, ui_consts.SPRITE_CONFIG_FILE)
        try:
            with open(config_path, consts.FILE_MODE_READ, encoding=consts.FILE_ENCODING) as f:
                text = f.read()
        except OSError:
            return ui_consts.SPRITE_DEFAULT_FPS, ui_consts.SPRITE_DEFAULT_IS_LOOP

        fps_match = _FPS_PATTERN.search(text)
        loop_match = _LOOP_PATTERN.search(text)
        fps = int(fps_match.group(1)) if fps_match else ui_consts.SPRITE_DEFAULT_FPS
        is_loop = (
            (loop_match.group(1) == ui_consts.SPRITE_JSON_TRUE)
            if loop_match else ui_consts.SPRITE_DEFAULT_IS_LOOP
        )
        return fps, is_loop

    @staticmethod
    def _read_frames(state_dir: str, dark: bool) -> list[Image.Image]:
        sprites_dir = os.path.join(state_dir, ui_consts.SPRITE_FRAMES_DIR)
        frames = []
        index = ui_consts.SPRITE_FIRST_FRAME_INDEX
        while True:
            path = os.path.join(sprites_dir, f"{index}{ui_consts.SPRITE_FRAME_EXTENSION}")
            if not os.path.isfile(path):
                break
            try:
                img = Image.open(path).convert(ui_consts.IMAGE_MODE_RGBA)
            except OSError:
                break
            frames.append(_to_black_and_white(img, dark))
            index += 1
        return frames

    def frame_for(
        self, piece_type: str, color: str, state: PieceVisualState, elapsed_millis: int
    ) -> Image.Image | None:
        animation = self._animations.get((piece_type, color, state))
        if animation is None or not animation.frames:
            return None

        index = elapsed_millis * animation.frames_per_sec // consts.MS_PER_SECOND
        if animation.is_loop:
            index = index % len(animation.frames)
        else:
            index = min(index, len(animation.frames) - 1)
        return animation.frames[index]

    def sized_frame_for(
        self, piece_type: str, color: str, state: PieceVisualState, elapsed_millis: int, size: int
    ) -> Image.Image | None:
        """Like frame_for, but returns a size x size frame, caching the resize.

        Source frames are loaded once at startup and never mutated, so the
        same (frame identity, size) pair always produces the same resized
        bitmap - safe to memoize across ticks instead of resampling every
        piece with Pillow on every 16ms redraw.
        """
        frame = self.frame_for(piece_type, color, state, elapsed_millis)
        if frame is None or size <= 0:
            return frame

        key = (id(frame), size)
        cached = self._resize_cache.get(key)
        if cached is None:
            cached = frame.resize((size, size))
            self._resize_cache[key] = cached
        return cached
