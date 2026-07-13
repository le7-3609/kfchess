"""GamePlayState — State pattern for active / game-over (Layer 5)."""

from abc import ABC, abstractmethod

from kungfu_chess.model.position import Position


class GamePlayState(ABC):
    """Represents the current play state (active or game-over)."""

    @abstractmethod
    def handle_click(self, engine: 'GameEngine', target: Position) -> None:
        """Handle a click in this state."""

    @abstractmethod
    def handle_jump(self, engine: 'GameEngine', target: Position) -> None:
        """Handle a jump in this state."""


class ActivePlayState(GamePlayState):
    def handle_click(self, engine: 'GameEngine', target: Position) -> None:
        engine._execute_active_click(target)

    def handle_jump(self, engine: 'GameEngine', target: Position) -> None:
        engine._execute_active_jump(target)


class GameOverPlayState(GamePlayState):
    def handle_click(self, engine: 'GameEngine', target: Position) -> None:
        pass  # Ignored after game over.

    def handle_jump(self, engine: 'GameEngine', target: Position) -> None:
        pass  # Ignored after game over.


class GamePlayStateFactory:
    def get_state(self, game_over: bool) -> GamePlayState:
        return GameOverPlayState() if game_over else ActivePlayState()
