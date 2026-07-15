# Repository: https://github.com/le7-3609/kfchess

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kungfu_chess.bootstrap import build_core
from kungfu_chess.config.game_config import GameConfig
from kungfu_chess.gui.tk_window import TkGameWindow
from kungfu_chess.io.board_parser import BoardParser
from kungfu_chess.io.board_validator import BoardValidator
from kungfu_chess.realtime.real_time_arbiter import ChebyshevDistanceDuration
from kungfu_chess.view.pillow_renderer import PillowRenderer
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
    config = GameConfig()
    core = build_core(config, require_kings=True, duration_strategy=ChebyshevDistanceDuration(config.ms_per_square))

    raw_board, _ = BoardParser().parse(_STARTING_POSITION.splitlines())
    validation = BoardValidator(require_kings=True).validate_and_build(raw_board)
    if not validation.is_ok:
        raise RuntimeError(f"Failed to build starting position: {validation.error}")
    core.board_repo.save_board(validation.value)

    renderer = PillowRenderer(os.path.join(_ASSETS_DIR, "pieces2"))
    snapshot_builder = SnapshotBuilder(engine=core.engine, arbiter=core.arbiter, config=config)

    window = TkGameWindow(
        engine=core.engine,
        board_repo=core.board_repo,
        state_repo=core.state_repo,
        renderer=renderer,
        snapshot_builder=snapshot_builder,
    )
    window.run()


if __name__ == "__main__":
    main()
