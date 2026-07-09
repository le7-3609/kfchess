"""Board mapper — pixel-coordinate to board-cell translation (Layer 6 input).

Owns: translation of raw pixel clicks into board (row, col) positions.
Must not own: chess legality, Board mutation, rendering, or timing.
"""

from typing import Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface


class BoardMapper:
    """Translates raw pixel coordinates to board positions.

    Uses a fixed cell size (in pixels) to map (x, y) -> (row, col).
    """

    def __init__(self, cell_size_px: int) -> None:
        """
        Args:
            cell_size_px: The width (and height) of a single board cell in pixels.
        """
        self._cell_size_px = cell_size_px

    def pixel_to_position(self, x: int, y: int, board: BoardInterface) -> Optional[Position]:
        """Convert pixel coordinates (x, y) to a board Position.

        Args:
            x: Horizontal pixel coordinate (column direction).
            y: Vertical pixel coordinate (row direction).
            board: The current board (used to validate the resulting position).

        Returns:
            A valid Position if (x, y) maps to an on-board cell, else None.
        """
        col = x // self._cell_size_px
        row = y // self._cell_size_px
        pos = Position(row, col)
        if board.is_valid_position(pos):
            return pos
        return None
