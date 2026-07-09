from abc import ABC, abstractmethod
from typing import Optional
from kfchess.models.interfaces import PieceInterface
from kfchess.models.board import Position
from kfchess.config.game_config import GameConfig

from kfchess.rules.interfaces import PromotionStrategyInterface

class StandardPawnPromotion(PromotionStrategyInterface):
    """Promotes a pawn to a queen upon reaching the farthest rank."""
    def evaluate_promotion(self, piece: PieceInterface, to_pos: Position, config: GameConfig) -> None:
        if piece.piece_type != "P":
            return
            
        player_config = config.get_player(piece.color)
        if not player_config:
            return
            
        # Standard chess: if forward is -1, back rank is 0. If forward is 1, back rank is rows - 1.
        target_row = 0 if player_config.forward_direction < 0 else config.board_rows - 1
        
        if to_pos.row == target_row:
            # We must mutate piece_type. Since TextPiece has it, we just set it.
            # We assume the implementation supports setting piece_type.
            if hasattr(piece, 'piece_type'):
                setattr(piece, 'piece_type', "Q")
