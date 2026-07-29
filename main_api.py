"""CLI entry point for the KungFu Chess HTTP API.

Serves game history, the leaderboard, token issuance and the operational
endpoints. Holds no game state and no player sockets, so it restarts freely:
clients simply retry.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from server.presentation.app_runner import run_api, run_entry_point

DESCRIPTION = "KungFu Chess HTTP API server"


def main() -> None:
    run_entry_point(DESCRIPTION, run_api)


if __name__ == "__main__":
    main()
