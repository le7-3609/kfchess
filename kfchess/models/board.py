from typing import List, NamedTuple, Optional

from kfchess.models.interfaces import BoardInterface, PieceInterface


class Position(NamedTuple):
    row: int
    col: int


class ArrayBoard(BoardInterface):
    """Textual/Array implementation of the board."""
    
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

# Alias Board to ArrayBoard for backward compatibility where name is hardcoded,
# though we aim to use BoardInterface mostly.
Board = ArrayBoard
