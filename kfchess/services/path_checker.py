"""Board-aware path and capture checker (Strategy pattern).

PathChecker is responsible for two concerns that require inspecting the live
board, which the geometry-only MoveValidatorInterface deliberately avoids:

1. **Path blocking** — sliding pieces (Rook, Bishop, Queen) cannot pass
   through a square occupied by any piece.  Non-sliding pieces (King, Knight)
   are exempt: the King moves only one square and the Knight jumps.

2. **Capture legality** — a piece may never land on a square occupied by a
   friendly piece, but may land on (and capture) an enemy piece.

PathChecker implements PathCheckerInterface and is injected into
CommandExecutor at the composition root (main.py).
"""

from typing import FrozenSet

from kfchess.models.board import Board, Position
from kfchess.models.piece import Piece, PieceType
from kfchess.services.interfaces import PathCheckerInterface

# Piece types whose movement traces a straight line that can be blocked.
_SLIDING_TYPES: FrozenSet[PieceType] = frozenset({
    PieceType.ROOK,
    PieceType.BISHOP,
    PieceType.QUEEN,
})


def _sign(n: int) -> int:
    """Return -1, 0, or +1 — the sign of *n*."""
    if n > 0:
        return 1
    if n < 0:
        return -1
    return 0


class PathChecker(PathCheckerInterface):
    """Concrete board-aware checker for path blocking and capture rules."""

    def is_path_clear(
        self,
        board: Board,
        frm: Position,
        to: Position,
    ) -> bool:
        """Return True if no piece occupies any square strictly between *frm* and *to*.

        Only sliding pieces (Rook, Bishop, Queen) can be blocked.
        King and Knight always return True: the King moves exactly one square
        (so there are no intermediate squares) and the Knight jumps over pieces.

        The check assumes the move is already geometrically valid (straight or
        diagonal line for sliders).
        """
        piece = board.get_piece(frm)
        if piece is None or piece.piece_type not in _SLIDING_TYPES:
            # Non-sliders are never blocked by intervening pieces.
            return True

        dr = _sign(to.row - frm.row)
        dc = _sign(to.col - frm.col)

        # Walk every intermediate square (exclude frm and to).
        cur = Position(frm.row + dr, frm.col + dc)
        while cur != to:
            if board.get_piece(cur) is not None:
                return False  # Blocked by an intervening piece.
            cur = Position(cur.row + dr, cur.col + dc)

        return True

    def can_land(
        self,
        board: Board,
        moving_piece: Piece,
        frm: Position,
        to: Position,
    ) -> bool:
        """Return True if *moving_piece* is allowed to land on square *to* from *frm*.

        Returns False only when *to* is occupied by a piece of the **same
        color** as *moving_piece* (friendly-fire is forbidden).
        For pawns/soldiers:
        - If moving forward (straight): target must be empty.
        - If moving diagonally: target must be occupied by an enemy piece.
        """
        occupant = board.get_piece(to)
        if occupant is not None and occupant.color == moving_piece.color:
            return False  # Cannot capture a friendly piece.

        if moving_piece.piece_type == PieceType.PAWN:
            col_diff = abs(to.col - frm.col)
            if col_diff == 0:
                # Forward move: destination must be empty
                if occupant is not None:
                    return False
            elif col_diff == 1:
                # Diagonal move: destination must contain an enemy piece (different color)
                if occupant is None:
                    return False
            else:
                return False

        return True

