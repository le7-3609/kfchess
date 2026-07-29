"""CLI entry point running both server roles in one process.

Convenient for local development and for the Compose stack, where one process
is simpler than two. It is *not* the deployed topology: see `main_ws.py` and
`main_api.py`, which run the same code as separate, independently scalable
processes. Running them together means a burst of replay queries — database
bound, CPU-heavy JSON serialization — competes for the event loop that is
supposed to be forwarding moves, and deploying a change to the leaderboard
disconnects every player mid-game.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.presentation.app_runner import run_combined, run_entry_point

DESCRIPTION = "KungFu Chess multiplayer server (WebSocket + HTTP API in one process)"


def main() -> None:
    run_entry_point(DESCRIPTION, run_combined)


if __name__ == "__main__":
    main()
