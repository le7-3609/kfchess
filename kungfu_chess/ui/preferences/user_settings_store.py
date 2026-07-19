"""Persists small player preferences (piece theme, board theme, movement
speed, and cooldown time) to a JSON file on disk, mirroring GameHistoryStore's
persistence style.
"""

import json
import os
from dataclasses import dataclass

from kungfu_chess.config import consts
from kungfu_chess.ui.preferences.piece_themes import DEFAULT_THEME_ID
from kungfu_chess.ui.preferences.board_themes import DEFAULT_THEME_ID as DEFAULT_BOARD_THEME_ID

_DEFAULT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", consts.USER_SETTINGS_FILE_NAME
)


@dataclass(frozen=True)
class UserSettings:
    piece_theme: str = DEFAULT_THEME_ID
    board_theme: str = DEFAULT_BOARD_THEME_ID
    speed_level_ms: int = consts.DEFAULT_SPEED_LEVEL_MS
    cooldown_level_ms: int = consts.DEFAULT_COOLDOWN_LEVEL_MS


class UserSettingsStore:
    def __init__(self, file_path: str = _DEFAULT_PATH):
        self._file_path = file_path

    def load(self) -> UserSettings:
        try:
            with open(self._file_path, consts.FILE_MODE_READ, encoding=consts.FILE_ENCODING) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return UserSettings()

        return UserSettings(
            piece_theme=data.get(consts.SETTINGS_KEY_PIECE_THEME, DEFAULT_THEME_ID),
            board_theme=data.get(consts.SETTINGS_KEY_BOARD_THEME, DEFAULT_BOARD_THEME_ID),
            speed_level_ms=data.get(
                consts.SETTINGS_KEY_SPEED_LEVEL_MS, consts.DEFAULT_SPEED_LEVEL_MS
            ),
            cooldown_level_ms=data.get(
                consts.SETTINGS_KEY_COOLDOWN_LEVEL_MS, consts.DEFAULT_COOLDOWN_LEVEL_MS
            ),
        )

    def save(self, settings: UserSettings) -> None:
        payload = {
            consts.SETTINGS_KEY_PIECE_THEME: settings.piece_theme,
            consts.SETTINGS_KEY_BOARD_THEME: settings.board_theme,
            consts.SETTINGS_KEY_SPEED_LEVEL_MS: settings.speed_level_ms,
            consts.SETTINGS_KEY_COOLDOWN_LEVEL_MS: settings.cooldown_level_ms,
        }
        with open(self._file_path, consts.FILE_MODE_WRITE, encoding=consts.FILE_ENCODING) as f:
            json.dump(payload, f, indent=consts.JSON_INDENT)
