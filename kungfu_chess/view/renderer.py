"""Renderer interface — visual drawing (Layer 6).

Owns: visual drawing from a read-only GameSnapshot.
Must not own: game rules, board mutation, input parsing, or text-test logic.
"""

from abc import ABC, abstractmethod

from kungfu_chess.view.game_snapshot import GameSnapshot


class RendererInterface(ABC):
    """Draws the current game state.

    Depends only on the read-only GameSnapshot DTO — never on GameState or
    BoardInterface directly — so drawing stays decoupled from board mutation.
    """

    @abstractmethod
    def draw(self, snapshot: GameSnapshot) -> None:
        pass


class NullRenderer(RendererInterface):

    def draw(self, snapshot: GameSnapshot) -> None:
        pass
