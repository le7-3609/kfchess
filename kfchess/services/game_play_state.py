from __future__ import annotations
from typing import TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from kfchess.services.command_executor import CommandExecutor


class GamePlayState(ABC):
    """Abstract base class representing the current game play state (State Pattern)."""

    @abstractmethod
    def handle_click(self, executor: CommandExecutor, x: int, y: int) -> None:
        """Handle a click command."""


class ActivePlayState(GamePlayState):
    """State where the game is active, so click commands are executed normally."""

    def handle_click(self, executor: CommandExecutor, x: int, y: int) -> None:
        executor._execute_active_click(x, y)


class GameOverPlayState(GamePlayState):
    """State where the game is over, so subsequent move/click commands are ignored."""

    def handle_click(self, executor: CommandExecutor, x: int, y: int) -> None:
        # Move commands are ignored after game over.
        pass


class GamePlayStateFactory:
    """Factory that returns the appropriate GamePlayState instance (Factory Pattern)."""

    def get_state(self, game_over: bool) -> GamePlayState:
        if game_over:
            return GameOverPlayState()
        return ActivePlayState()
