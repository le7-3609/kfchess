"""Controller — click interpretation and selected-cell state (Layer 6 input).

Owns: selected-cell state, translating click events into game commands.
Must not own: chess legality, Board mutation, rendering, or timing.

The Controller acts as a thin translation layer between raw UI events and
the GameEngine's command API.  It forwards fully-formed command strings
(e.g. ``"click 150 200"``) to the engine so the engine can dispatch them.
"""

from typing import Optional

from kungfu_chess.model.position import Position
from kungfu_chess.input.board_mapper import BoardMapper


class Controller:
    """Interprets raw pixel clicks and maintains selected-cell state.

    The Controller is responsible for:
    - Tracking which cell (if any) is currently selected.
    - Converting (x, y) pixel events into engine commands.

    It intentionally knows nothing about chess rules or board mutation.
    """

    def __init__(self, board_mapper: BoardMapper) -> None:
        self._board_mapper = board_mapper
        self._selected_pos: Optional[Position] = None

    @property
    def selected_pos(self) -> Optional[Position]:
        """The currently selected board position, or None."""
        return self._selected_pos

    def on_click(self, x: int, y: int) -> str:
        """Translate a pixel click into a ``"click x y"`` engine command string.

        The engine's GameEngine.execute_command() handles all actual selection
        and movement logic; the Controller simply formats and forwards the event.

        Returns:
            A command string like ``"click 150 200"``.
        """
        return f"click {x} {y}"

    def on_jump(self, x: int, y: int) -> str:
        """Translate a pixel click-on-selected-cell into a ``"jump x y"`` command.

        Returns:
            A command string like ``"jump 150 200"``.
        """
        return f"jump {x} {y}"
