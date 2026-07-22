"""Algebraic notation <-> Position conversion for the network client (client layer).

Owns: translating board Position values to/from algebraic square notation
("e2") for the wire protocol server/application/dtos defines.
Must not own: network I/O, GUI state, or game rules.

The client package may not import from server/ (see CLAUDE.md's dependency
direction), so this mirrors
server.application.dtos.protocol_mapper.AlgebraicParser independently on the
client side of the wire boundary rather than importing it.
"""

from shared.config import consts
from shared.model.position import Position

# A square identifier is exactly file letter + rank digit, e.g. "e2".
_SQUARE_TOKEN_LENGTH = 2


def parse_square(square: str) -> Position:
    """Parse an algebraic square identifier like 'e2' into a Position.

    Raises:
        ValueError: If square is malformed or out of board bounds.
    """
    if not isinstance(square, str) or len(square) != _SQUARE_TOKEN_LENGTH:
        raise ValueError(f"Invalid square notation: {square!r}")

    file_char = square[0].lower()
    rank_char = square[1]

    if file_char not in consts.NOTATION_FILES:
        raise ValueError(f"Invalid file letter in square {square!r}")

    try:
        rank_num = int(rank_char)
    except ValueError as exc:
        raise ValueError(f"Invalid rank number in square {square!r}") from exc

    if not (1 <= rank_num <= consts.NOTATION_RANKS):
        raise ValueError(f"Rank number out of bounds in square {square!r}")

    col = consts.NOTATION_FILES.index(file_char)
    row = consts.NOTATION_RANKS - rank_num
    return Position(row=row, col=col)


def format_square(pos: Position) -> str:
    """Format a Position(row, col) into algebraic square notation like 'e2'."""
    if not (0 <= pos.col < len(consts.NOTATION_FILES)):
        raise ValueError(f"Position col out of bounds: {pos.col}")
    if not (0 <= pos.row < consts.NOTATION_RANKS):
        raise ValueError(f"Position row out of bounds: {pos.row}")

    file_char = consts.NOTATION_FILES[pos.col]
    rank_num = consts.NOTATION_RANKS - pos.row
    return f"{file_char}{rank_num}"
