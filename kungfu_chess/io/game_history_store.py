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

_DEFAULT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", consts.SAVED_GAMES_DIR_NAME
)
_SAFE_NAME = re.compile(consts.SAVE_NAME_SAFE_PATTERN)


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
        timestamp = datetime.now().strftime(consts.SAVE_TIMESTAMP_FORMAT)
        file_name = f"{_sanitize(save_name)}_{timestamp}{consts.SAVE_FILE_EXTENSION}"
        file_path = os.path.join(self._directory, file_name)

        payload = self._build_payload(
            save_name, white_name, black_name, winner, moves_log, timestamp, speed_ms, cooldown_ms
        )
        with open(file_path, consts.FILE_MODE_WRITE, encoding=consts.FILE_ENCODING) as f:
            json.dump(payload, f, indent=consts.JSON_INDENT)
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
            consts.SAVE_KEY_NAME: save_name,
            consts.SAVE_KEY_WHITE_NAME: white_name,
            consts.SAVE_KEY_BLACK_NAME: black_name,
            consts.SAVE_KEY_WINNER: winner or "",
            consts.SAVE_KEY_SAVED_AT: timestamp,
            consts.SAVE_KEY_SPEED_MS: speed_ms,
            consts.SAVE_KEY_COOLDOWN_MS: cooldown_ms,
            consts.SAVE_KEY_MOVES: [
                {
                    consts.SAVE_KEY_MOVE_COLOR: entry.color,
                    consts.SAVE_KEY_MOVE_NOTATION: entry.notation,
                    consts.SAVE_KEY_MOVE_TIME: entry.time_ms,
                }
                for entry in moves_log.entries()
            ],
        }

    def list_saves(self) -> List[str]:
        """Names of every saved game file, newest first."""
        if not os.path.isdir(self._directory):
            return []
        names = [
            name for name in os.listdir(self._directory)
            if name.endswith(consts.SAVE_FILE_EXTENSION)
        ]
        names.sort(reverse=True)
        return names

    def load(self, file_name: str) -> SavedGame:
        file_path = os.path.join(self._directory, file_name)
        with open(file_path, consts.FILE_MODE_READ, encoding=consts.FILE_ENCODING) as f:
            data = json.load(f)

        moves = [
            MoveLogEntry(
                color=m[consts.SAVE_KEY_MOVE_COLOR],
                notation=m[consts.SAVE_KEY_MOVE_NOTATION],
                time_ms=m[consts.SAVE_KEY_MOVE_TIME],
            )
            for m in data.get(consts.SAVE_KEY_MOVES, [])
        ]
        return SavedGame(
            save_name=data.get(consts.SAVE_KEY_NAME, ""),
            white_name=data.get(consts.SAVE_KEY_WHITE_NAME, ""),
            black_name=data.get(consts.SAVE_KEY_BLACK_NAME, ""),
            winner=data.get(consts.SAVE_KEY_WINNER) or None,
            saved_at=data.get(consts.SAVE_KEY_SAVED_AT, ""),
            moves=moves,
            speed_ms=data.get(consts.SAVE_KEY_SPEED_MS, consts.DEFAULT_MS_PER_SQUARE),
            cooldown_ms=data.get(consts.SAVE_KEY_COOLDOWN_MS, consts.DEFAULT_COOLDOWN_DURATION_MS),
        )


def _sanitize(name: str) -> str:
    trimmed = (name or "").strip()
    if not trimmed:
        trimmed = consts.DEFAULT_SAVE_NAME
    return _SAFE_NAME.sub(consts.SAVE_NAME_REPLACEMENT, trimmed)
