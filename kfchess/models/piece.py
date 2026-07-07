from enum import Enum
from typing import Optional


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
    def __init__(self, color: Color, piece_type: PieceType) -> None:
        self.color = color
        self.piece_type = piece_type

    def __str__(self) -> str:
        return f"{self.color.value}{self.piece_type.value}"

    def __repr__(self) -> str:
        return f"Piece({self.color.name}, {self.piece_type.name})"

    def __eq__(self, other: object) -> bool:
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
