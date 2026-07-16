# Repository: https://github.com/le7-3609/kfchess

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kungfu_chess.bootstrap import build_core
from kungfu_chess.config.game_config import GameConfig
from kungfu_chess.config.piece_themes import get_theme
from kungfu_chess.gui.pillow_renderer import PillowRenderer
from kungfu_chess.gui.tk_window import TkGameWindow
from kungfu_chess.io.board_parser import BoardParser
from kungfu_chess.io.board_validator import BoardValidator
from kungfu_chess.io.game_history_store import GameHistoryStore
from kungfu_chess.io.moves_log import MovesLog
from kungfu_chess.io.user_settings_store import UserSettingsStore
from kungfu_chess.realtime.real_time_arbiter import ChebyshevDistanceDuration
from kungfu_chess.view.snapshot_builder import SnapshotBuilder

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
    core = build_core(config, require_kings=True, duration_strategy=ChebyshevDistanceDuration(config.ms_per_square))

    raw_board, _ = BoardParser().parse(_STARTING_POSITION.splitlines())
    validation = BoardValidator(require_kings=True).validate_and_build(raw_board)
    if not validation.is_ok:
        raise RuntimeError(f"Failed to build starting position: {validation.error}")
    core.board_repo.save_board(validation.value)

    settings_store = UserSettingsStore()
    theme = get_theme(settings_store.load().piece_theme)
    renderer = PillowRenderer(os.path.join(_ASSETS_DIR, theme.folder_name))
    snapshot_builder = SnapshotBuilder(engine=core.engine, arbiter=core.arbiter, config=config)

    moves_log = MovesLog(clock_ms=lambda: core.state_repo.get_state().clock_ms)
    core.move_event_publisher.subscribe(moves_log)
    history_store = GameHistoryStore()

    window = TkGameWindow(
        engine=core.engine,
        board_repo=core.board_repo,
        state_repo=core.state_repo,
        renderer=renderer,
        snapshot_builder=snapshot_builder,
        white_name=white_name,
        black_name=black_name,
        history_store=history_store,
        moves_log=moves_log,
        assets_dir=_ASSETS_DIR,
        settings_store=settings_store,
    )
    window.run()


if __name__ == "__main__":
    main()
