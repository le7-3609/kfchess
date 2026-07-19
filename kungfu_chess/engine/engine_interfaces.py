"""GameEngine collaborator interfaces (Layer 5).

Defined here so the engine layer can decouple from input/, storage, and
rendering: those packages implement these interfaces, the dependency never
points the other way.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.game_state import GameState
from kungfu_chess.engine.input_commands import GameCommand


class InputSourceInterface(ABC):
    """An automated or external source of player commands (e.g. a bot).

    Defined here (Layer 4) rather than imported from input/ (Layer 6) so the
    dependency points inward: input.RandomBotInputSource implements this
    interface, it is never the other way around.
    """

    @abstractmethod
    def get_next_commands(self) -> List[GameCommand]:
        """Return the next batch of commands to execute, if any."""


class PixelMapperInterface(ABC):
    """Translates raw pixel coordinates into board positions.

    Defined here (Layer 4) rather than imported from input/ (Layer 6) so the
    dependency points inward: input.BoardMapper implements this interface,
    it is never the other way around.
    """

    @abstractmethod
    def pixel_to_position(self, x: int, y: int, board: BoardInterface) -> Optional[Position]:
        """Convert pixel coordinates (x, y) to a board Position, or None if off-board."""


class BoardRepositoryInterface(ABC):
    @abstractmethod
    def get_board(self) -> Optional[BoardInterface]:
        """Retrieve the currently stored board."""

    @abstractmethod
    def save_board(self, board: BoardInterface) -> None:
        """Persist the given board."""


class GameStateRepositoryInterface(ABC):
    @abstractmethod
    def get_state(self) -> GameState:
        """Retrieve the current game state."""

    @abstractmethod
    def save_state(self, state: GameState) -> None:
        """Persist the given game state."""


class BoardPrinterInterface(ABC):
    @abstractmethod
    def print_board(self, board: BoardInterface) -> None:
        """Write the board layout to an output stream."""
