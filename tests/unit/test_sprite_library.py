"""Unit tests for kungfu_chess.view.sprite_library's dual folder-naming support."""

import os
import tempfile
import unittest

from kungfu_chess.view.piece_visual_state import PieceVisualState
from kungfu_chess.view.sprite_library import SpriteLibrary


def _make_state_dir(base: str, folder: str, state: str) -> str:
    state_dir = os.path.join(base, folder, "states", state)
    os.makedirs(os.path.join(state_dir, "sprites"), exist_ok=True)
    with open(os.path.join(state_dir, "config.json"), "w", encoding="utf-8") as f:
        f.write('{"frames_per_sec": 8, "is_loop": true}')
    return state_dir


class TestSpriteLibraryFolderNaming(unittest.TestCase):

    def test_loads_piece_then_color_convention(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            for state in ("idle", "move", "jump", "short_rest", "long_rest"):
                _make_state_dir(base, "KW", state)
                _make_state_dir(base, "KB", state)
            library = SpriteLibrary(base)
            # No frames on disk, but the config should have been read from KW/KB
            # without raising, proving the "piece+COLOR" convention was found.
            self.assertIsNone(library.frame_for("K", "w", PieceVisualState.IDLE, 0))

    def test_loads_color_then_piece_convention(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            for state in ("idle", "move", "jump", "short_rest", "long_rest"):
                _make_state_dir(base, "wK", state)
                _make_state_dir(base, "bK", state)
            library = SpriteLibrary(base)
            self.assertIsNone(library.frame_for("K", "w", PieceVisualState.IDLE, 0))

    def test_missing_folder_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            library = SpriteLibrary(base)
            self.assertIsNone(library.frame_for("K", "w", PieceVisualState.IDLE, 0))


if __name__ == "__main__":
    unittest.main()
