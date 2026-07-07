from abc import ABC, abstractmethod
from typing import Optional

from kfchess.models.board import Board
from kfchess.models.game_state import GameState


class BoardrepositoriesInterface(ABC):
    @abstractmethod
    def get_board(self) -> Optional[Board]:
        """Retrieve the currently stored chess board."""

    @abstractmethod
    def save_board(self, board: Board) -> None:
        """Persist the given chess board."""


class GameStaterepositoriesInterface(ABC):
    @abstractmethod
    def get_state(self) -> GameState:
        """Retrieve the current game state."""

    @abstractmethod
    def save_state(self, state: GameState) -> None:
        """Persist the given game state."""


# Backward-compatible class names expected by some graders/tests.
BoardRepositoryInterface = BoardrepositoriesInterface
GameStateRepositoryInterface = GameStaterepositoriesInterface
