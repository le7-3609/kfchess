"""Board model — BoardInterface and ArrayBoard implementation.

Owns: board coordinates, piece identity, logical occupancy.
Must not own: pixels, clicks, rendering, script parsing, movement rules, or timing.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from shared.errors import EmptyCellError, InvalidPositionError, OccupiedCellError
from shared.model.position import Position
from shared.model.piece import PieceInterface


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
    def find_position(self, piece: PieceInterface) -> Optional[Position]:
        """Return the position of *piece* on the board (by identity), or None if not present."""

    @abstractmethod
    def set_piece(self, pos: Position, piece: Optional[PieceInterface]) -> None:
        """Place or remove a piece at *pos*."""

    @abstractmethod
    def add_piece(self, pos: Position, piece: PieceInterface) -> None:
        """Place *piece* at *pos*. Raises OccupiedCellError if *pos* is already occupied."""

    @abstractmethod
    def remove_piece(self, pos: Position) -> Optional[PieceInterface]:
        """Clear *pos* and return the piece that was there, or None if it was empty."""

    @abstractmethod
    def move_piece(self, frm: Position, to: Position) -> Optional[PieceInterface]:
        """Relocate the piece at *frm* to *to*, assuming the move has already been validated.

        Returns whatever piece previously occupied *to* (e.g. a captured piece),
        or None if *to* was empty. Raises EmptyCellError if *frm* has no piece.
        """


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
            raise InvalidPositionError(pos)
        return self._grid[pos.row][pos.col]

    def find_position(self, piece: PieceInterface) -> Optional[Position]:
        for r in range(self._rows):
            for c in range(self._cols):
                if self._grid[r][c] is piece:
                    return Position(r, c)
        return None

    def set_piece(self, pos: Position, piece: Optional[PieceInterface]) -> None:
        if not self.is_valid_position(pos):
            raise InvalidPositionError(pos)
        self._grid[pos.row][pos.col] = piece

    def add_piece(self, pos: Position, piece: PieceInterface) -> None:
        if not self.is_valid_position(pos):
            raise InvalidPositionError(pos)
        if self._grid[pos.row][pos.col] is not None:
            raise OccupiedCellError(pos)
        self._grid[pos.row][pos.col] = piece

    def remove_piece(self, pos: Position) -> Optional[PieceInterface]:
        if not self.is_valid_position(pos):
            raise InvalidPositionError(pos)
        piece = self._grid[pos.row][pos.col]
        self._grid[pos.row][pos.col] = None
        return piece

    def move_piece(self, frm: Position, to: Position) -> Optional[PieceInterface]:
        if not self.is_valid_position(frm) or not self.is_valid_position(to):
            raise InvalidPositionError(frm if not self.is_valid_position(frm) else to)
        piece = self._grid[frm.row][frm.col]
        if piece is None:
            raise EmptyCellError(frm)
        captured = self._grid[to.row][to.col]
        self._grid[to.row][to.col] = piece
        self._grid[frm.row][frm.col] = None
        return captured
