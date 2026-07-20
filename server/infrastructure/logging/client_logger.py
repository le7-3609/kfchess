"""Client log ingestion service — accepts and persists client-side activity logs.

Owns: validating and appending client-submitted log batches to disk for debugging.
Must not own: network sockets or server event handling.
"""

import json
import logging
import os
import time
from typing import Any, Dict, List

_LOGGER = logging.getLogger("shared.client_logger")


class ClientLogIngestor:
    """Ingests client-side log entries and appends them to disk."""

    def __init__(self, log_dir: str = "server_logs/client_logs") -> None:
        self._log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

    def ingest_client_logs(self, username: str, entries: List[Dict[str, Any]]) -> int:
        """Ingest a batch of client log entries for a given user.

        Returns:
            Number of successfully written entries.
        """
        if not isinstance(entries, list):
            return 0

        filename = os.path.join(self._log_dir, f"client_{username}.jsonl")
        count = 0

        try:
            with open(filename, "a", encoding="utf-8") as f:
                for entry in entries:
                    if isinstance(entry, dict):
                        record = {
                            "server_received_at": time.time(),
                            "username": username,
                            "client_entry": entry,
                        }
                        f.write(json.dumps(record) + "\n")
                        count += 1
        except Exception as exc:
            _LOGGER.warning("Failed to write client logs for %s: %s", username, exc)

        return count
