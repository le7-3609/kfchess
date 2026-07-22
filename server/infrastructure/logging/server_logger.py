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

from shared.config.consts import FILE_ENCODING, FILE_MODE_APPEND, LINE_SEPARATOR
from shared.events import Event, Observer

_LOGGER = logging.getLogger("server_logger")

DEFAULT_SERVER_LOG_DIR = "server_logs"

# Structured log entry keys.
_KEY_TIMESTAMP = "timestamp"
_KEY_EVENT_TYPE = "event_type"
_KEY_AT_MS = "at_ms"
_KEY_CATEGORY = "category"
_KEY_DIRECTION = "direction"
_KEY_USERNAME = "username"
_KEY_FRAME = "frame"
_CATEGORY_NETWORK_FRAME = "network_frame"

# Attribute probes for duck-typed event objects.
_ATTR_AT_MS = "at_ms"
_ATTR_DICT = "__dict__"
_PRIVATE_FIELD_PREFIX = "_"


class ServerLogger(Observer):
    """Observer that records game domain events to a structured log file."""

    def __init__(self, log_dir: str = DEFAULT_SERVER_LOG_DIR) -> None:
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
            _KEY_TIMESTAMP: time.time(),
            _KEY_EVENT_TYPE: type(event).__name__,
            _KEY_AT_MS: getattr(event, _ATTR_AT_MS, 0),
        }

        # Extract dataclass fields
        for field, val in getattr(event, _ATTR_DICT, {}).items():
            if not field.startswith(_PRIVATE_FIELD_PREFIX):
                entry[field] = str(val) if not isinstance(val, (int, float, bool, str, type(None))) else val

        self.record_log_entry(entry)

    def record_log_entry(self, entry: Dict[str, Any]) -> None:
        """Write structured entry to internal list and file."""
        self._log_entries.append(entry)
        try:
            with open(self._log_file_path, FILE_MODE_APPEND, encoding=FILE_ENCODING) as f:
                f.write(json.dumps(entry) + LINE_SEPARATOR)
        except Exception as exc:
            _LOGGER.warning("Failed to append server log entry: %s", exc)

    def log_network_frame(self, direction: str, username: str, frame_data: Dict[str, Any]) -> None:
        """Record incoming/outgoing WebSocket frames."""
        self.record_log_entry({
            _KEY_TIMESTAMP: time.time(),
            _KEY_CATEGORY: _CATEGORY_NETWORK_FRAME,
            _KEY_DIRECTION: direction,  # "in" or "out"
            _KEY_USERNAME: username,
            _KEY_FRAME: frame_data,
        })
