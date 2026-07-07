from typing import List, NamedTuple, Optional

from kfchess.models.piece import Piece


class Position(NamedTuple):
    row: int
    col: int


class Board:
    def __init__(self, rows: int, cols: int) -> None:
        self.rows = rows
        self.cols = cols
        self._grid: List[List[Optional[Piece]]] = [
            [None for _ in range(cols)] for _ in range(rows)
        ]

    def is_valid_position(self, pos: Position) -> bool:
        return 0 <= pos.row < self.rows and 0 <= pos.col < self.cols

    def get_piece(self, pos: Position) -> Optional[Piece]:
        if not self.is_valid_position(pos):
            raise IndexError("Position out of board bounds.")
        return self._grid[pos.row][pos.col]

    def set_piece(self, pos: Position, piece: Optional[Piece]) -> None:
        if not self.is_valid_position(pos):
            raise IndexError("Position out of board bounds.")
        self._grid[pos.row][pos.col] = piece

    def get_row_tokens(self, row_idx: int) -> List[str]:
        if not (0 <= row_idx < self.rows):
            raise IndexError("Row index out of board bounds.")
        return [
            '.' if piece is None else str(piece)
            for piece in self._grid[row_idx]
        ]
