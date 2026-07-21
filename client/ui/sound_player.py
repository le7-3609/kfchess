"""SoundPlayer — fires the match's short audio cues (Layer 6 / client UI).

Owns: locating the `.wav` cue files under the assets folder and asking the
platform to play one without blocking the Tk loop.
Must not own: deciding *when* a cue is warranted — that judgment belongs to
GameWindow, which knows the domain event it is reacting to.
"""

import logging
import os
import sys
from typing import Optional

from client.ui import consts as ui_consts

_LOGGER = logging.getLogger(__name__)

# winsound is stdlib but Windows-only; other platforms get a silent no-op
# rather than a new bundled audio dependency this project doesn't otherwise need.
if sys.platform == "win32":
    import winsound
else:
    winsound = None


class SoundPlayer:
    """Plays a cue by name from *assets_dir*/sounds, or does nothing if unavailable."""

    def __init__(self, assets_dir: Optional[str]) -> None:
        self._sounds_dir = (
            os.path.join(assets_dir, ui_consts.SOUNDS_DIR_NAME) if assets_dir else None
        )

    def play_move(self) -> None:
        self._play(ui_consts.SOUND_FILE_MOVE)

    def play_win(self) -> None:
        self._play(ui_consts.SOUND_FILE_WIN)

    def play_lose(self) -> None:
        self._play(ui_consts.SOUND_FILE_LOSE)

    def _play(self, file_name: str) -> None:
        if self._sounds_dir is None or winsound is None:
            return
        path = os.path.join(self._sounds_dir, file_name)
        try:
            winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        except OSError:
            _LOGGER.warning("Could not play sound %s", path)
