"""Unit tests for GameHistoryStore — persistence of a game's move history."""

import json
import os
import tempfile
import unittest

from kungfu_chess.config import consts
from kungfu_chess.events import PieceMovedEvent
from kungfu_chess.io.game_history_store import GameHistoryStore
from kungfu_chess.io.moves_log import MovesLog
from kungfu_chess.model.position import Position


class _StoreTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.store = GameHistoryStore(self._dir.name)

    def _log_with_one_move(self) -> MovesLog:
        log = MovesLog()
        log.on_event(PieceMovedEvent(
            at_ms=2000,
            color="w",
            piece_type="P",
            frm=Position(6, 4),
            to=Position(4, 4),
            was_capture=False,
        ))
        return log

    def _write_raw(self, file_name: str, payload: dict) -> None:
        with open(os.path.join(self._dir.name, file_name), "w", encoding="utf-8") as f:
            json.dump(payload, f)


class TestSaveLoadRoundTrip(_StoreTestCase):
    def test_round_trips_moves_and_metadata(self):
        path = self.store.save("my game", "Alice", "Bob", "w", self._log_with_one_move())
        loaded = self.store.load(os.path.basename(path))

        self.assertEqual(loaded.save_name, "my game")
        self.assertEqual(loaded.white_name, "Alice")
        self.assertEqual(loaded.black_name, "Bob")
        self.assertEqual(loaded.winner, "w")
        self.assertEqual([m.notation for m in loaded.moves], ["Pe2-e4"])
        self.assertEqual(loaded.moves[0].time_ms, 2000)

    def test_round_trips_the_speed_and_cooldown_in_force(self):
        # Without these, a move's arrival timestamp cannot be read back into a
        # start time, since both are player-adjustable.
        path = self.store.save(
            "g", "A", "B", None, self._log_with_one_move(), speed_ms=600, cooldown_ms=1600
        )
        loaded = self.store.load(os.path.basename(path))
        self.assertEqual(loaded.speed_ms, 600)
        self.assertEqual(loaded.cooldown_ms, 1600)

    def test_no_winner_round_trips_as_none(self):
        path = self.store.save("g", "A", "B", None, self._log_with_one_move())
        self.assertIsNone(self.store.load(os.path.basename(path)).winner)


class TestBackwardCompatibility(_StoreTestCase):
    """Saves written before speed/cooldown were recorded must still load."""

    def test_save_without_speed_falls_back_to_the_defaults(self):
        self._write_raw(
            "old_2020-01-01_00-00-00.json",
            {
                "saveName": "old",
                "whiteName": "A",
                "blackName": "B",
                "winner": "",
                "savedAt": "2020-01-01_00-00-00",
                "moves": [{"color": "w", "notation": "Pe2-e4", "time": 2000}],
            },
        )
        loaded = self.store.load("old_2020-01-01_00-00-00.json")

        self.assertEqual(loaded.speed_ms, consts.DEFAULT_MS_PER_SQUARE)
        self.assertEqual(loaded.cooldown_ms, consts.DEFAULT_COOLDOWN_DURATION_MS)
        self.assertEqual([m.notation for m in loaded.moves], ["Pe2-e4"])


class TestListSaves(_StoreTestCase):
    def test_lists_newest_first(self):
        self._write_raw("a_2020-01-01_00-00-00.json", {})
        self._write_raw("b_2021-01-01_00-00-00.json", {})
        self.assertEqual(
            self.store.list_saves(), ["b_2021-01-01_00-00-00.json", "a_2020-01-01_00-00-00.json"]
        )

    def test_missing_directory_lists_nothing(self):
        self.assertEqual(GameHistoryStore(os.path.join(self._dir.name, "nope")).list_saves(), [])


if __name__ == "__main__":
    unittest.main()
