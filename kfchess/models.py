from enum import Enum
from typing import List, Optional, NamedTuple

class Color(Enum):
    WHITE = 'w'
    BLACK = 'b'

    @classmethod
    def has_value(cls, value: str) -> bool:
        return any(value == item.value for item in cls)


class PieceType(Enum):
    KING = 'K'
    QUEEN = 'Q'
    ROOK = 'R'
    BISHOP = 'B'
    KNIGHT = 'N'
    PAWN = 'P'

    @classmethod
    def has_value(cls, value: str) -> bool:
        return any(value == item.value for item in cls)


class Piece:
    def __init__(self, color: Color, piece_type: PieceType):
        self.color = color
        self.piece_type = piece_type

    def __str__(self) -> str:
        return f"{self.color.value}{self.piece_type.value}"

    def __repr__(self) -> str:
        return f"Piece({self.color.name}, {self.piece_type.name})"

    def __eq__(self, other) -> bool:
        if not isinstance(other, Piece):
            return False
        return self.color == other.color and self.piece_type == other.piece_type

    @classmethod
    def from_string(cls, token: str) -> Optional['Piece']:
        if len(token) != 2:
            return None
        color_char, piece_char = token[0], token[1]
        if not Color.has_value(color_char) or not PieceType.has_value(piece_char):
            return None
        return cls(Color(color_char), PieceType(piece_char))


class Position(NamedTuple):
    row: int
    col: int


class Board:
    def __init__(self, rows: int, cols: int):
        self.rows = rows
        self.cols = cols
        self._grid: List[List[Optional[Piece]]] = [[None for _ in range(cols)] for _ in range(rows)]

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
        tokens = []
        for piece in self._grid[row_idx]:
            if piece is None:
                tokens.append('.')
            else:
                tokens.append(str(piece))
        return tokens
