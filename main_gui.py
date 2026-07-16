# Repository: https://github.com/le7-3609/kfchess

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kungfu_chess.bootstrap import build_realtime_service
from kungfu_chess.config.game_config import GameConfig
from kungfu_chess.ui.preferences.piece_themes import get_theme
from kungfu_chess.ui.preferences.user_settings_store import UserSettingsStore
from kungfu_chess.ui.rendering.pillow_renderer import PillowRenderer
from kungfu_chess.ui.window.tk_window import TkGameWindow

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

_STARTING_POSITION = """
Board:
bR bN bB bQ bK bB bN bR
bP bP bP bP bP bP bP bP
.  .  .  .  .  .  .  .
.  .  .  .  .  .  .  .
.  .  .  .  .  .  .  .
.  .  .  .  .  .  .  .
wP wP wP wP wP wP wP wP
wR wN wB wQ wK wB wN wR
"""


def main() -> None:
    prompt_root = tk.Tk()
    prompt_root.withdraw()
    white_name = simpledialog.askstring("Player name", "White player name:", parent=prompt_root) or "White"
    black_name = simpledialog.askstring("Player name", "Black player name:", parent=prompt_root) or "Black"
    prompt_root.destroy()

    config = GameConfig()
    service = build_realtime_service(config=config, require_kings=True)

    init_result = service.init_game(_STARTING_POSITION.splitlines())
    if not init_result.is_ok:
        raise RuntimeError(f"Failed to build starting position: {init_result.error}")

    settings_store = UserSettingsStore()
    theme = get_theme(settings_store.load().piece_theme)
    renderer = PillowRenderer(os.path.join(_ASSETS_DIR, theme.folder_name))

    window = TkGameWindow(
        service=service,
        renderer=renderer,
        white_name=white_name,
        black_name=black_name,
        assets_dir=_ASSETS_DIR,
        settings_store=settings_store,
    )
    window.run()


if __name__ == "__main__":
    main()
