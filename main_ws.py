"""CLI entry point for the KungFu Chess WebSocket game server.

One of two roles built from the same image. This one holds long-lived player
sockets and runs the game simulation; `main_api.py` serves the HTTP API. They
are separate processes because restarting this one disconnects every player
mid-game, while restarting that one costs nothing — so they must be deployable
and scalable independently.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.presentation.app_runner import run_entry_point, run_ws

DESCRIPTION = "KungFu Chess WebSocket game server"


def main() -> None:
    run_entry_point(DESCRIPTION, run_ws)


if __name__ == "__main__":
    main()
