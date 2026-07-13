"""In-memory repository implementations, used by the composition root (bootstrap.py)."""

from kungfu_chess.model.game_state import GameState
from kungfu_chess.engine.game_engine import BoardRepositoryInterface, GameStateRepositoryInterface


class _InMemoryBoardRepo(BoardRepositoryInterface):
    def __init__(self) -> None:
        self._board = None

    def get_board(self):
        return self._board

    def save_board(self, board) -> None:
        self._board = board


class _InMemoryStateRepo(GameStateRepositoryInterface):
    def __init__(self) -> None:
        self._state: GameState = GameState()

    def get_state(self) -> GameState:
        return self._state

    def save_state(self, state: GameState) -> None:
        self._state = state
