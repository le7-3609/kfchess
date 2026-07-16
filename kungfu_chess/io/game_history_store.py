"""Persists a game's move history to a small JSON file on disk, and
lists/reloads past saves - the "save game" / "history list" counterpart
to the in-memory MovesLog kept during play.
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from kungfu_chess.config import consts
from kungfu_chess.io.moves_log import MoveLogEntry, MovesLog

_DEFAULT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "saved_games")
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class SavedGame:
    save_name: str
    white_name: str
    black_name: str
    winner: Optional[str]
    saved_at: str
    moves: List[MoveLogEntry] = field(default_factory=list)
    # Both are player-adjustable settings, so a move's timestamp cannot be
    # interpreted without them: replay reconstructs when a move *started* from
    # its arrival time and the speed in force. Saves written before these were
    # recorded fall back to the defaults.
    speed_ms: int = consts.DEFAULT_MS_PER_SQUARE
    cooldown_ms: int = consts.DEFAULT_COOLDOWN_DURATION_MS


class GameHistoryStore:
    """Reads and writes saved games as JSON files in a directory."""

    def __init__(self, directory: str = _DEFAULT_DIR):
        self._directory = directory

    def save(
        self,
        save_name: str,
        white_name: str,
        black_name: str,
        winner: Optional[str],
        moves_log: MovesLog,
        speed_ms: int = consts.DEFAULT_MS_PER_SQUARE,
        cooldown_ms: int = consts.DEFAULT_COOLDOWN_DURATION_MS,
    ) -> str:
        """Write *moves_log* and its game metadata to a new JSON file.

        The file is named from *save_name* plus a timestamp. Returns the path
        it was written to.
        """
        os.makedirs(self._directory, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        file_path = os.path.join(self._directory, f"{_sanitize(save_name)}_{timestamp}.json")

        payload = self._build_payload(
            save_name, white_name, black_name, winner, moves_log, timestamp, speed_ms, cooldown_ms
        )
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        return file_path

    def _build_payload(
        self,
        save_name: str,
        white_name: str,
        black_name: str,
        winner: Optional[str],
        moves_log: MovesLog,
        timestamp: str,
        speed_ms: int,
        cooldown_ms: int,
    ) -> dict:
        """Assemble the on-disk JSON shape for a saved game."""
        return {
            "saveName": save_name,
            "whiteName": white_name,
            "blackName": black_name,
            "winner": winner or "",
            "savedAt": timestamp,
            "speedMs": speed_ms,
            "cooldownMs": cooldown_ms,
            "moves": [
                {"color": entry.color, "notation": entry.notation, "time": entry.time_ms}
                for entry in moves_log.entries()
            ],
        }

    def list_saves(self) -> List[str]:
        """Names of every saved game file, newest first."""
        if not os.path.isdir(self._directory):
            return []
        names = [name for name in os.listdir(self._directory) if name.endswith(".json")]
        names.sort(reverse=True)
        return names

    def load(self, file_name: str) -> SavedGame:
        file_path = os.path.join(self._directory, file_name)
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        moves = [
            MoveLogEntry(color=m["color"], notation=m["notation"], time_ms=m["time"])
            for m in data.get("moves", [])
        ]
        return SavedGame(
            save_name=data.get("saveName", ""),
            white_name=data.get("whiteName", ""),
            black_name=data.get("blackName", ""),
            winner=data.get("winner") or None,
            saved_at=data.get("savedAt", ""),
            moves=moves,
            speed_ms=data.get("speedMs", consts.DEFAULT_MS_PER_SQUARE),
            cooldown_ms=data.get("cooldownMs", consts.DEFAULT_COOLDOWN_DURATION_MS),
        )


def _sanitize(name: str) -> str:
    trimmed = (name or "").strip()
    if not trimmed:
        trimmed = "game"
    return _SAFE_NAME.sub("_", trimmed)
