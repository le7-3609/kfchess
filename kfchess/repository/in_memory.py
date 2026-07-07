from typing import Optional

from kfchess.models.board import Board
from kfchess.models.game_state import GameState
from kfchess.repository.interfaces import BoardRepositoryInterface, GameStateRepositoryInterface


class InMemoryBoardRepository(BoardRepositoryInterface):
    def __init__(self) -> None:
        self._board: Optional[Board] = None

    def get_board(self) -> Optional[Board]:
        return self._board

    def save_board(self, board: Board) -> None:
        self._board = board


class InMemoryGameStateRepository(GameStateRepositoryInterface):
    def __init__(self) -> None:
        self._state: GameState = GameState()

    def get_state(self) -> GameState:
        return self._state

    def save_state(self, state: GameState) -> None:
        self._state = state
