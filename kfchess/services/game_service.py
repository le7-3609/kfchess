from typing import List

from kfchess.models.game_state import GameState
from kfchess.models.result import Result
from kfchess.repository.interfaces import BoardRepositoryInterface, GameStateRepositoryInterface
from kfchess.services.interfaces import (
    BoardParserInterface,
    BoardValidatorInterface,
    CommandExecutorInterface,
)


class GameService:
    """
    Thin orchestrator that wires together parsing, validation, and command
    execution.  All heavy logic lives in the injected collaborators.
    """

    def __init__(
        self,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        parser: BoardParserInterface,
        validator: BoardValidatorInterface,
        command_executor: CommandExecutorInterface,
    ) -> None:
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._parser = parser
        self._validator = validator
        self._command_executor = command_executor

    def execute(self, input_lines: List[str]) -> 'Result[None, str]':
        # 1. Parse
        raw_board, commands = self._parser.parse(input_lines)
        if not raw_board:
            return Result.ok(None)

        # 2. Validate & build
        validation = self._validator.validate_and_build(raw_board)
        if not validation.is_ok:
            return Result.fail(validation.error)

        # 3. Initialise repositories
        self._board_repo.save_board(validation.value)
        self._state_repo.save_state(GameState())

        # 4. Execute commands
        for cmd in commands:
            self._command_executor.execute_command(cmd)

        return Result.ok(None)
