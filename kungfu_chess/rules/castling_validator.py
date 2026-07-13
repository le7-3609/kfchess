"""Castling validator — legality of castling moves (Layer 3).

Must not own: Movement scheduling, animation, click interpretation, or
game-over state.
"""

from typing import NamedTuple, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.rules.threat_validator import ThreatValidator


class CastlingDestinations(NamedTuple):
    king_dest: Position
    rook_dest: Position


class CastlingValidator:
    """Determines whether a king/rook pair may legally castle."""

    def __init__(self, threat_validator: ThreatValidator, config: 'GameConfig') -> None:  # type: ignore[name-defined]
        self._threat_validator = threat_validator
        self._config = config

    def is_castle_attempt(
        self,
        king_piece: PieceInterface,
        rook_piece: PieceInterface,
        king_pos: Position,
        rook_pos: Position,
    ) -> bool:
        """Return True if clicking rook_piece while king_piece is selected represents a castle attempt."""
        return (
            king_piece.piece_type in self._config.king_pieces
            and rook_piece.piece_type == "R"
            and not king_piece.has_moved
            and not rook_piece.has_moved
            and king_pos.row == rook_pos.row
            and king_piece.can_move()
            and rook_piece.can_move()
        )

    def get_legal_castle(
        self,
        board: BoardInterface,
        king_pos: Position,
        rook_pos: Position,
        king_piece: PieceInterface,
    ) -> Optional[CastlingDestinations]:
        """Return destination squares if castling king_pos<->rook_pos is legal, else None."""
        dc = 1 if rook_pos.col > king_pos.col else -1

        if not self._is_path_clear(board, king_pos, rook_pos, dc):
            return None

        king_dest = Position(king_pos.row, king_pos.col + 2 * dc)
        rook_dest = Position(rook_pos.row, king_pos.col + 1 * dc)

        if not board.is_valid_position(king_dest) or not board.is_valid_position(rook_dest):
            return None

        if not self._squares_safe(board, king_pos, king_piece, dc, king_dest):
            return None

        return CastlingDestinations(king_dest, rook_dest)

    def _is_path_clear(
        self,
        board: BoardInterface,
        king_pos: Position,
        rook_pos: Position,
        dc: int,
    ) -> bool:
        cur_col = king_pos.col + dc
        while cur_col != rook_pos.col:
            if board.get_piece(Position(king_pos.row, cur_col)) is not None:
                return False
            cur_col += dc
        return True

    def _squares_safe(
        self,
        board: BoardInterface,
        king_pos: Position,
        king_piece: PieceInterface,
        dc: int,
        king_dest: Position,
    ) -> bool:
        """True if the king is safe at king_pos and every square it passes through en route to king_dest."""
        pass_pos = Position(king_pos.row, king_pos.col + dc)
        for pos_to_check in (king_pos, pass_pos, king_dest):
            if not self._threat_validator.is_move_safe_from_check(board, king_pos, pos_to_check, king_piece):
                return False
        return True
