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


class GameHistoryStore:
    def __init__(self, directory: str = _DEFAULT_DIR):
        self._directory = directory

    def save(
        self,
        save_name: str,
        white_name: str,
        black_name: str,
        winner: Optional[str],
        moves_log: MovesLog,
    ) -> str:
        os.makedirs(self._directory, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe_name = _sanitize(save_name)
        file_name = f"{safe_name}_{timestamp}.json"
        file_path = os.path.join(self._directory, file_name)

        payload = {
            "saveName": save_name,
            "whiteName": white_name,
            "blackName": black_name,
            "winner": winner or "",
            "savedAt": timestamp,
            "moves": [
                {"color": entry.color, "notation": entry.notation, "time": entry.time_ms}
                for entry in moves_log.entries()
            ],
        }

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return file_path

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
        )


def _sanitize(name: str) -> str:
    trimmed = (name or "").strip()
    if not trimmed:
        trimmed = "game"
    return _SAFE_NAME.sub("_", trimmed)
