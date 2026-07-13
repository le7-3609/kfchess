from abc import ABC, abstractmethod

from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.game_state import GameState


class RendererInterface(ABC):

    @abstractmethod
    def draw(self, board: BoardInterface, state: GameState) -> None:
        pass


class NullRenderer(RendererInterface):

    def draw(self, board: BoardInterface, state: GameState) -> None:
        pass
