"""Persists small player preferences (piece theme, board theme, movement
speed, and cooldown time) to a JSON file on disk, mirroring GameHistoryStore's
persistence style.
"""

import json
import os
from dataclasses import dataclass

from kungfu_chess.ui.preferences.piece_themes import DEFAULT_THEME_ID
from kungfu_chess.ui.preferences.board_themes import DEFAULT_THEME_ID as DEFAULT_BOARD_THEME_ID

_DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "user_settings.json")

DEFAULT_SPEED_MS = 1000
DEFAULT_COOLDOWN_MS = 50000


@dataclass(frozen=True)
class UserSettings:
    piece_theme: str = DEFAULT_THEME_ID
    board_theme: str = DEFAULT_BOARD_THEME_ID
    speed_level_ms: int = DEFAULT_SPEED_MS
    cooldown_level_ms: int = DEFAULT_COOLDOWN_MS


class UserSettingsStore:
    def __init__(self, file_path: str = _DEFAULT_PATH):
        self._file_path = file_path

    def load(self) -> UserSettings:
        try:
            with open(self._file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return UserSettings()

        return UserSettings(
            piece_theme=data.get("pieceTheme", DEFAULT_THEME_ID),
            board_theme=data.get("boardTheme", DEFAULT_BOARD_THEME_ID),
            speed_level_ms=data.get("speedLevelMs", DEFAULT_SPEED_MS),
            cooldown_level_ms=data.get("cooldownLevelMs", DEFAULT_COOLDOWN_MS),
        )

    def save(self, settings: UserSettings) -> None:
        payload = {
            "pieceTheme": settings.piece_theme,
            "boardTheme": settings.board_theme,
            "speedLevelMs": settings.speed_level_ms,
            "cooldownLevelMs": settings.cooldown_level_ms,
        }
        with open(self._file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
