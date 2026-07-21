# Repository: https://github.com/le7-3609/kfchess

import asyncio
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from client.auth.cli_auth import prompt_authentication
from client.ui import consts as ui_consts
from client.ui.preferences.user_settings_store import UserSettingsStore
from client.ui.window.lobby_window import LobbyWindow

_ASSETS_DIR = os.path.join(
    os.path.dirname(__file__), "ui", ui_consts.ASSETS_DIR_NAME
)

DEFAULT_SERVER_URL = "ws://localhost:8765"


def main() -> None:
    server_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SERVER_URL

    credentials = asyncio.run(prompt_authentication(server_url))

    settings_store = UserSettingsStore()

    lobby = LobbyWindow(
        server_url=server_url,
        credentials=credentials,
        assets_dir=_ASSETS_DIR,
        settings_store=settings_store,
    )
    lobby.run()


if __name__ == "__main__":
    main()
