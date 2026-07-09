"""Renderer — draws the current game state using a supplied drawing library.

Owns: visual drawing from a read-only board/state snapshot.
Must not own: game rules, Board mutation, input parsing, or text-test logic.

This module provides a base interface and a headless no-op implementation.
A real visual renderer would subclass RendererInterface and implement draw().
"""

from abc import ABC, abstractmethod
from typing import Optional

from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.game_state import GameState


class RendererInterface(ABC):
    """Abstract renderer — draws the current game state."""

    @abstractmethod
    def draw(self, board: BoardInterface, state: GameState) -> None:
        """Render the board and game state to the display."""


class NullRenderer(RendererInterface):
    """No-op renderer for headless / text-test use."""

    def draw(self, board: BoardInterface, state: GameState) -> None:
        pass  # Nothing to render in headless mode.
