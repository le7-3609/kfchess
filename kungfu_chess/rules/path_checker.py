"""Path checker — path-blocking and capture legality (Layer 3).

Must not own: board mutation, animation, click interpretation, game-over state transitions.
"""

from typing import FrozenSet, List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface


def _sign(n: int) -> int:
    """Return -1, 0, or +1 — the sign of *n*."""
    if n > 0:
        return 1
    if n < 0:
        return -1
    return 0


class PathCheckerInterface:
    """Board-aware validator for path-blocking and capture legality (abstract)."""

    def is_path_clear(self, board: BoardInterface, frm: Position, to: Position) -> bool:  # type: ignore[empty-body]
        raise NotImplementedError

    def can_land(
        self,
        board: BoardInterface,
        moving_piece: PieceInterface,
        frm: Position,
        to: Position,
        en_passant_targets: Optional[List[Position]] = None,
    ) -> bool:  # type: ignore[empty-body]
        raise NotImplementedError


# Piece types whose movement traces a straight line that can be blocked.
_SLIDING_TYPES: FrozenSet[str] = frozenset(("R", "B", "Q", "P"))


class PathChecker(PathCheckerInterface):
    """Concrete board-aware checker for path-blocking and capture rules."""

    def is_path_clear(
        self,
        board: BoardInterface,
        frm: Position,
        to: Position,
    ) -> bool:
        """Return True if no piece occupies any square strictly between *frm* and *to*.

        Only sliding pieces (Rook, Bishop, Queen, Pawn) can be blocked.
        Knights always return True because they jump over pieces.
        The King moves only one square so there are never intermediate squares.
        """
        piece = board.get_piece(frm)
        if piece is None or piece.piece_type not in _SLIDING_TYPES:
            return True

        dr = _sign(to.row - frm.row)
        dc = _sign(to.col - frm.col)

        cur = Position(frm.row + dr, frm.col + dc)
        while cur != to:
            if board.get_piece(cur) is not None:
                return False
            cur = Position(cur.row + dr, cur.col + dc)

        return True

    def can_land(
        self,
        board: BoardInterface,
        moving_piece: PieceInterface,
        frm: Position,
        to: Position,
        en_passant_targets: Optional[List[Position]] = None,
    ) -> bool:
        """Return True if *moving_piece* is allowed to land on *to*.

        - Never land on a friendly piece.
        - Pawn forward move: destination must be empty.
        - Pawn diagonal move: destination must have an enemy, or be an en-passant target.
        """
        occupant = board.get_piece(to)
        if occupant is not None and occupant.color == moving_piece.color:
            return False

        if moving_piece.piece_type == "P":
            col_diff = abs(to.col - frm.col)
            if col_diff == 0:
                if occupant is not None:
                    return False
            elif col_diff == 1:
                if occupant is None:
                    if en_passant_targets is not None and to in en_passant_targets:
                        return True
                    return False
            else:
                return False

        return True
