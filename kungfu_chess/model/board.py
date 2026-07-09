"""Board model — BoardInterface and ArrayBoard implementation.

Owns: board coordinates, piece identity, logical occupancy.
Must not own: pixels, clicks, rendering, script parsing, movement rules, or timing.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.piece import PieceInterface


# ---------------------------------------------------------------------------
# Board interface
# ---------------------------------------------------------------------------

class BoardInterface(ABC):
    """Abstract interface for a game board.

    Decouples the storage representation (Array, Bitboard …) from game logic.
    """

    @property
    @abstractmethod
    def rows(self) -> int:
        """Number of rows on the board."""

    @property
    @abstractmethod
    def cols(self) -> int:
        """Number of columns on the board."""

    @abstractmethod
    def is_valid_position(self, pos: Position) -> bool:
        """Return True if *pos* is within the board boundaries."""

    @abstractmethod
    def get_piece(self, pos: Position) -> Optional[PieceInterface]:
        """Return the piece at *pos*, or None if the square is empty."""

    @abstractmethod
    def set_piece(self, pos: Position, piece: Optional[PieceInterface]) -> None:
        """Place or remove a piece at *pos*."""


# ---------------------------------------------------------------------------
# Concrete implementation
# ---------------------------------------------------------------------------

class ArrayBoard(BoardInterface):
    """2-D list implementation of the board."""

    def __init__(self, rows: int, cols: int) -> None:
        self._rows = rows
        self._cols = cols
        self._grid: List[List[Optional[PieceInterface]]] = [
            [None for _ in range(cols)] for _ in range(rows)
        ]

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def cols(self) -> int:
        return self._cols

    def is_valid_position(self, pos: Position) -> bool:
        return 0 <= pos.row < self._rows and 0 <= pos.col < self._cols

    def get_piece(self, pos: Position) -> Optional[PieceInterface]:
        if not self.is_valid_position(pos):
            raise IndexError("Position out of board bounds.")
        return self._grid[pos.row][pos.col]

    def set_piece(self, pos: Position, piece: Optional[PieceInterface]) -> None:
        if not self.is_valid_position(pos):
            raise IndexError("Position out of board bounds.")
        self._grid[pos.row][pos.col] = piece


# Alias for backward-compatibility.
Board = ArrayBoard
