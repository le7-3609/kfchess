"""Unit tests for kungfu_chess.ui.preferences.user_settings_store."""

import os
import tempfile
import unittest

from kungfu_chess.ui.preferences.piece_themes import DEFAULT_THEME_ID
from kungfu_chess.ui.preferences.user_settings_store import UserSettings, UserSettingsStore


class TestUserSettingsStore(unittest.TestCase):

    def test_load_missing_file_returns_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = UserSettingsStore(os.path.join(tmp, "user_settings.json"))
            self.assertEqual(store.load(), UserSettings(piece_theme=DEFAULT_THEME_ID))

    def test_save_then_load_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "user_settings.json")
            store = UserSettingsStore(path)
            store.save(UserSettings(piece_theme="pieces_mine"))
            self.assertEqual(store.load(), UserSettings(piece_theme="pieces_mine"))

    def test_corrupt_file_falls_back_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "user_settings.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("not json")
            store = UserSettingsStore(path)
            self.assertEqual(store.load(), UserSettings(piece_theme=DEFAULT_THEME_ID))


if __name__ == "__main__":
    unittest.main()
