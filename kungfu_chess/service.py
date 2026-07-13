"""GameService — thin orchestrator wiring parse/validate/build/execute together."""

from typing import List

from kungfu_chess.config.game_config import GameConfig
from kungfu_chess.model.game_state import GameState, Result
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.engine.game_engine import BoardRepositoryInterface, GameStateRepositoryInterface, GameEngine
from kungfu_chess.engine.engine_interfaces import InputSourceInterface
from kungfu_chess.io.board_parser import BoardParser
from kungfu_chess.io.board_validator import BoardValidator


class GameService:
    """Thin orchestrator: parse, validate, build board, execute commands."""

    def __init__(
        self,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        parser: BoardParser,
        validator: BoardValidator,
        engine: GameEngine,
        bot: InputSourceInterface = None,
        config: GameConfig = None,
    ) -> None:
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._parser = parser
        self._validator = validator
        self._engine = engine
        self._bot = bot
        self._config = config

    def execute(self, input_lines: List[str]) -> Result:
        raw_board, commands = self._parser.parse(input_lines)
        if not raw_board:
            return Result.ok(None)

        validation = self._validator.validate_and_build(raw_board)
        if not validation.is_ok:
            return Result.fail(validation.error)

        board = validation.value
        self._board_repo.save_board(board)
        self._state_repo.save_state(GameState())

        self._adjust_pawn_rules_for_board_height(board)

        for cmd in commands:
            self._engine.execute_command(cmd)
            self._trigger_bot_reaction_if_active()

        return Result.ok(None)

    def _adjust_pawn_rules_for_board_height(self, board: BoardInterface) -> None:
        if not self._config:
            return
        self._config.board_rows = board.rows
        self._config.board_cols = board.cols
        w_player = self._config.get_player("w")
        b_player = self._config.get_player("b")
        if board.rows == 8:
            if w_player:
                w_player.pawn_start_rows = [6]
                w_player.promotion_rank = 0
            if b_player:
                b_player.pawn_start_rows = [1]
                b_player.promotion_rank = 7
        else:
            if w_player:
                w_player.pawn_start_rows = [board.rows - 1]
                w_player.promotion_rank = 0
            if b_player:
                b_player.pawn_start_rows = [0]
                b_player.promotion_rank = board.rows - 1

    def _trigger_bot_reaction_if_active(self) -> None:
        if self._bot and not self._state_repo.get_state().game_over:
            bot_cmds = self._bot.get_next_commands()
            for b_cmd in bot_cmds:
                self._engine.execute_command(b_cmd)
