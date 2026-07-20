"""Bot strategy input source (Layer 5/Input).

Acts as an automated player issuing commands to the engine.
Belongs in the input layer as it simulates user input without
violating the rules or board layers.
"""

import random
from typing import List

from shared.model.position import Position
from shared.rules.rule_engine import EndgameValidator
from shared.engine.game_engine import BoardRepositoryInterface, GameStateRepositoryInterface
from shared.engine.engine_interfaces import InputSourceInterface
from shared.engine.input_commands import GameCommand, RequestMoveCommand


class RandomBotInputSource(InputSourceInterface):
    """A bot that selects a random legal move for its color."""

    def __init__(
        self,
        color: str,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        endgame_validator: EndgameValidator,
        config,
    ) -> None:
        self._color = color
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._endgame_validator = endgame_validator
        self._config = config

    def get_next_commands(self) -> List[GameCommand]:
        board = self._board_repo.get_board()
        if board is None:
            return []
        state = self._state_repo.get_state()
        if state.game_over:
            return []

        valid_moves = self._endgame_validator.get_legal_moves(board, state, self._color)
        if not valid_moves:
            return []

        src, dst = random.choice(valid_moves)
        return [RequestMoveCommand(source=src, target=dst)]

