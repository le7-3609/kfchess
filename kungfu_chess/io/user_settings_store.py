"""Persists small player preferences (currently: chosen piece theme) to a
JSON file on disk, mirroring GameHistoryStore's persistence style.
"""

import json
import os
from dataclasses import dataclass

from kungfu_chess.config.piece_themes import DEFAULT_THEME_ID

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "user_settings.json")


@dataclass(frozen=True)
class UserSettings:
    piece_theme: str = DEFAULT_THEME_ID


class UserSettingsStore:
    def __init__(self, file_path: str = _DEFAULT_PATH):
        self._file_path = file_path

    def load(self) -> UserSettings:
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return UserSettings()

        return UserSettings(piece_theme=data.get("pieceTheme", DEFAULT_THEME_ID))

    def save(self, settings: UserSettings) -> None:
        payload = {"pieceTheme": settings.piece_theme}
        with open(self._file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
