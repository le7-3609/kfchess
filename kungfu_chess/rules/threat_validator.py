"""Threat validator — king-in-check detection (Layer 3).

Must not own: board mutation, animation, click interpretation, game-over state transitions.
"""

from typing import Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.rules.piece_rules import MoveValidatorFactoryInterface
from kungfu_chess.rules.path_checker import PathCheckerInterface


class ThreatValidator:
    """Validates whether a king of a given color is under threat by any enemy piece."""

    def __init__(
        self,
        move_validator_factory: MoveValidatorFactoryInterface,
        path_checker: PathCheckerInterface,
        config: 'GameConfig',  # type: ignore[name-defined]
    ) -> None:
        self._move_validator_factory = move_validator_factory
        self._path_checker = path_checker
        self._config = config

    def find_king(self, board: BoardInterface, color: str) -> Optional[Position]:
        """Locate the king of *color* on *board* using configured king pieces."""
        for r in range(board.rows):
            for c in range(board.cols):
                pos = Position(r, c)
                piece = board.get_piece(pos)
                if piece is not None and piece.piece_type in self._config.king_pieces and piece.color == color:
                    return pos
        return None

    def is_king_threatened(self, board: BoardInterface, color: str) -> bool:
        """Return True if the king of *color* is threatened by any enemy piece on *board*."""
        king_pos = self.find_king(board, color)
        if king_pos is None:
            return False

        for r in range(board.rows):
            for c in range(board.cols):
                enemy_pos = Position(r, c)
                enemy_piece = board.get_piece(enemy_pos)
                if enemy_piece is None or enemy_piece.color == color:
                    continue
                if self._is_piece_threatening_target(board, enemy_pos, enemy_piece, king_pos):
                    return True
        return False

    def _is_piece_threatening_target(
        self,
        board: BoardInterface,
        enemy_pos: Position,
        enemy_piece: PieceInterface,
        target_pos: Position
    ) -> bool:
        validator = self._move_validator_factory.get_validator(enemy_piece.piece_type)
        if not validator.is_legal(enemy_pos, target_pos, enemy_piece.color, board.rows):
            return False
        return (self._path_checker.is_path_clear(board, enemy_pos, target_pos)
                and self._path_checker.can_land(board, enemy_piece, enemy_pos, target_pos))
