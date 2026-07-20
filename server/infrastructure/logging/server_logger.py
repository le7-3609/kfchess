"""Server logging observer — records comprehensive structured event and network activity logs.

Owns: structured log recording (JSON format) for all EventBus events, network frames,
matchmaker actions, and game outcomes.
Must not own: game state mutation or network protocol transport.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from shared.events import Event, Observer

_LOGGER = logging.getLogger("server_logger")


class ServerLogger(Observer):
    """Observer that records game domain events to a structured log file."""

    def __init__(self, log_dir: str = "server_logs") -> None:
        self._log_dir = log_dir
        self._log_entries: List[Dict[str, Any]] = []
        os.makedirs(log_dir, exist_ok=True)
        self._log_file_path = os.path.join(log_dir, f"server_{int(time.time())}.jsonl")

    @property
    def log_entries(self) -> List[Dict[str, Any]]:
        return list(self._log_entries)

    def on_event(self, event: Event) -> None:
        """Record domain events published on EventBus."""
        entry = {
            "timestamp": time.time(),
            "event_type": type(event).__name__,
            "at_ms": getattr(event, "at_ms", 0),
        }

        # Extract dataclass fields
        for field, val in getattr(event, "__dict__", {}).items():
            if not field.startswith("_"):
                entry[field] = str(val) if not isinstance(val, (int, float, bool, str, type(None))) else val

        self.record_log_entry(entry)

    def record_log_entry(self, entry: Dict[str, Any]) -> None:
        """Write structured entry to internal list and file."""
        self._log_entries.append(entry)
        try:
            with open(self._log_file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as exc:
            _LOGGER.warning("Failed to append server log entry: %s", exc)

    def log_network_frame(self, direction: str, username: str, frame_data: Dict[str, Any]) -> None:
        """Record incoming/outgoing WebSocket frames."""
        self.record_log_entry({
            "timestamp": time.time(),
            "category": "network_frame",
            "direction": direction,  # "in" or "out"
            "username": username,
            "frame": frame_data,
        })
