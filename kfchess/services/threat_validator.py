from typing import List
from kfchess.models.board import Position
from kfchess.models.interfaces import BoardInterface
from kfchess.rules.interfaces import MoveValidatorFactoryInterface, PathCheckerInterface
from kfchess.config.game_config import GameConfig

class ThreatValidator:
    """Validates if a king is under threat by any enemy piece."""

    def __init__(
        self,
        move_validator_factory: MoveValidatorFactoryInterface,
        path_checker: PathCheckerInterface,
        config: GameConfig,
    ) -> None:
        self._move_validator_factory = move_validator_factory
        self._path_checker = path_checker
        self._config = config

    def is_king_threatened(self, board: BoardInterface, color: str) -> bool:
        """
        Return True if the king of the given color is threatened by any enemy piece
        currently on the board.
        """
        # 1. Find the King's position
        king_pos = None
        for r in range(board.rows):
            for c in range(board.cols):
                pos = Position(r, c)
                piece = board.get_piece(pos)
                if piece is not None and piece.piece_type in self._config.king_pieces and piece.color == color:
                    king_pos = pos
                    break
            if king_pos is not None:
                break
        
        if king_pos is None:
            return False  # No king found, cannot be threatened

        # 2. Iterate through all enemy pieces to see if they can attack the king
        for r in range(board.rows):
            for c in range(board.cols):
                enemy_pos = Position(r, c)
                enemy_piece = board.get_piece(enemy_pos)
                
                if enemy_piece is None or enemy_piece.color == color:
                    continue

                # Check if this enemy can legally move to the king's square
                validator = self._move_validator_factory.get_validator(enemy_piece.piece_type)
                
                # Check basic shape validation
                if not validator.is_legal(enemy_pos, king_pos, enemy_piece.color, board.rows):
                    continue
                
                # Check path clarity and landing validation
                if self._path_checker.is_path_clear(board, enemy_pos, king_pos) and \
                   self._path_checker.can_land(board, enemy_piece, enemy_pos, king_pos):
                    return True
        
        return False
