"""Sprite library — loads and animates per-piece sprite sheets (Layer 6).

Owns: reading assets/pieces2/<code>/states/<state>/{config.json,sprites/*.png}
from disk and picking the correct animation frame for a piece's current
visual state and elapsed time.
Must not own: game rules, board mutation, input parsing, or timing
advancement (elapsed_millis is handed in, never measured here).
"""

import os
import re

from PIL import Image

from kungfu_chess.view.piece_visual_state import PieceVisualState

_FPS_PATTERN = re.compile(r'"frames_per_sec"\s*:\s*(-?\d+)')
_LOOP_PATTERN = re.compile(r'"is_loop"\s*:\s*(true|false)')

_STATE_NAMES = {
    PieceVisualState.IDLE: "idle",
    PieceVisualState.MOVE: "move",
    PieceVisualState.JUMP: "jump",
    PieceVisualState.SHORT_REST: "short_rest",
    PieceVisualState.LONG_REST: "long_rest",
}

# kungfu_chess pieces are plain ("w"/"b", "K"/"Q"/"R"/"B"/"N"/"P") strings;
# the sprite sheets are organised as <PIECE_LETTER><COLOR_LETTER>, e.g. "KW".
_PIECE_TYPES = ("K", "Q", "R", "B", "N", "P")
_COLORS = ("w", "b")


class _Animation:
    def __init__(self, frames: list[Image.Image], frames_per_sec: int, is_loop: bool):
        self.frames = frames
        self.frames_per_sec = frames_per_sec
        self.is_loop = is_loop


def _folder_name_candidates(piece_type: str, color: str) -> list[str]:
    """Different asset sets use different folder-naming conventions, e.g.
    "KW"/"KB" (piece letter + uppercase color) vs "wK"/"bK" (lowercase color
    + piece letter). Try both so any theme folder can be dropped in as-is."""
    upper_color = "W" if color == "w" else "B"
    return [f"{piece_type}{upper_color}", f"{color}{piece_type}"]


def _to_black_and_white(src: Image.Image, dark: bool) -> Image.Image:
    """Remap every pixel's luminance into a narrow band so pieces render as strict
    black/white silhouettes regardless of the source sprite sheet's actual palette,
    preserving the original alpha channel."""
    src = src.convert("RGBA")
    range_low = 12 if dark else 205
    range_span = 55

    lut = [max(0, min(255, range_low + round((lum / 255) * range_span))) for lum in range(256)]

    luminance = src.convert("RGB").convert("L", matrix=(0.299, 0.587, 0.114, 0))
    remapped = luminance.point(lut)
    alpha = src.getchannel("A")

    out = Image.merge("RGBA", (remapped, remapped, remapped, alpha))
    return out


class SpriteLibrary:
    def __init__(self, base_path: str):
        self._base_path = base_path
        self._animations: dict[tuple[str, str, PieceVisualState], _Animation] = {}

        for piece_type in _PIECE_TYPES:
            for color in _COLORS:
                for state in PieceVisualState:
                    self._load(piece_type, color, state)

    def _load(self, piece_type: str, color: str, state: PieceVisualState) -> None:
        state_dir = None
        for folder in _folder_name_candidates(piece_type, color):
            candidate = os.path.join(self._base_path, folder, "states", _STATE_NAMES[state])
            if os.path.isdir(candidate):
                state_dir = candidate
                break
        if state_dir is None:
            state_dir = os.path.join(
                self._base_path, _folder_name_candidates(piece_type, color)[0], "states", _STATE_NAMES[state]
            )

        frames_per_sec, is_loop = self._read_config(state_dir)
        frames = self._read_frames(state_dir, dark=(color == "b"))

        self._animations[(piece_type, color, state)] = _Animation(frames, frames_per_sec, is_loop)

    @staticmethod
    def _read_config(state_dir: str) -> tuple[int, bool]:
        config_path = os.path.join(state_dir, "config.json")
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            return 8, True

        fps_match = _FPS_PATTERN.search(text)
        loop_match = _LOOP_PATTERN.search(text)
        fps = int(fps_match.group(1)) if fps_match else 8
        is_loop = (loop_match.group(1) == "true") if loop_match else True
        return fps, is_loop

    @staticmethod
    def _read_frames(state_dir: str, dark: bool) -> list[Image.Image]:
        sprites_dir = os.path.join(state_dir, "sprites")
        frames = []
        index = 1
        while True:
            path = os.path.join(sprites_dir, f"{index}.png")
            if not os.path.isfile(path):
                break
            try:
                img = Image.open(path).convert("RGBA")
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

        index = elapsed_millis * animation.frames_per_sec // 1000
        if animation.is_loop:
            index = index % len(animation.frames)
        else:
            index = min(index, len(animation.frames) - 1)
        return animation.frames[index]
