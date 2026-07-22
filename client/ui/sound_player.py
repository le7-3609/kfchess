"""SoundPlayer — fires the match's ending audio cues (Layer 6 / client UI).

Owns: locating the `.wav` cue files under the assets folder, validating they
are playable, and asking the platform to play one without blocking the Tk loop.
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

_RIFF_MAGIC = b"RIFF"


class SoundPlayer:
    """Plays a cue by name from *assets_dir*/sounds, or does nothing if unavailable."""

    def __init__(self, assets_dir: Optional[str]) -> None:
        self._sounds_dir = (
            os.path.join(assets_dir, ui_consts.SOUNDS_DIR_NAME) if assets_dir else None
        )

    def play_win(self) -> None:
        self._play(ui_consts.SOUND_FILE_WIN)

    def play_lose(self) -> None:
        self._play(ui_consts.SOUND_FILE_LOSE)

    def _play(self, file_name: str) -> None:
        path = self._resolve_playable(file_name)
        if path is None:
            return
        flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        try:
            winsound.PlaySound(path, flags)
        except OSError:
            _LOGGER.warning("Could not play sound %s", path)

    def _resolve_playable(self, file_name: str) -> Optional[str]:
        """Return the cue's full path, or None if it cannot be played.

        winsound substitutes the Windows default error chime for any file it
        cannot decode; validating existence and the RIFF header here (plus
        SND_NODEFAULT at play time) keeps a bad asset silent instead of noisy.
        """
        if self._sounds_dir is None or winsound is None:
            return None
        path = os.path.join(self._sounds_dir, file_name)
        if not self._is_riff_wave(path):
            _LOGGER.warning("Sound cue %s is missing or not a WAV file; skipping", path)
            return None
        return path

    @staticmethod
    def _is_riff_wave(path: str) -> bool:
        try:
            with open(path, "rb") as cue_file:
                return cue_file.read(len(_RIFF_MAGIC)) == _RIFF_MAGIC
        except OSError:
            return False
