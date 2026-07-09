from typing import List, Optional
from kfchess.models.interfaces import BoardInterface
from kfchess.models.board import Position
from kfchess.models.game_state import GameState
from kfchess.services.threat_validator import ThreatValidator
from kfchess.rules.interfaces import MoveValidatorFactoryInterface, PathCheckerInterface
from kfchess.config.game_config import GameConfig
from kfchess.services.interfaces import MovementManagerInterface

def serialize_board_state(board: BoardInterface, state: GameState) -> str:
    lines = []
    for r in range(board.rows):
        row_str = []
        for c in range(board.cols):
            p = board.get_piece(Position(r, c))
            row_str.append(str(p) if p is not None else ".")
        lines.append(" ".join(row_str))
    board_part = "\n".join(lines)
    
    ep_part = ",".join(sorted(f"{ep.pos.row},{ep.pos.col}" for ep in state.en_passant_targets))
    
    castling_list = []
    for r in range(board.rows):
        for c in range(board.cols):
            p = board.get_piece(Position(r, c))
            if p is not None and p.piece_type in ["K", "R"]:
                castling_list.append(f"{r},{c},{p.color},{p.piece_type},{p.has_moved}")
    castling_part = ";".join(sorted(castling_list))
    
    return f"{board_part}|{ep_part}|{castling_part}"


class EndgameValidator:
    """Evaluates the board state to detect Checkmate and Stalemate conditions."""

    def __init__(
        self,
        move_validator_factory: MoveValidatorFactoryInterface,
        path_checker: PathCheckerInterface,
        movement_manager: MovementManagerInterface,
        threat_validator: ThreatValidator,
        config: GameConfig,
    ) -> None:
        self._move_validator_factory = move_validator_factory
        self._path_checker = path_checker
        self._movement_manager = movement_manager
        self._threat_validator = threat_validator
        self._config = config

    def _has_any_legal_move(self, board: BoardInterface, state: GameState, color: str) -> bool:
        # Get effective board with all active movements applied
        eff_board = self._movement_manager.get_effective_board(board, state, state.clock_ms)
        en_passant_targets = [ep.pos for ep in state.en_passant_targets]
        
        # Iterate all squares to find pieces of this color that can move
        for r in range(eff_board.rows):
            for c in range(eff_board.cols):
                pos = Position(r, c)
                piece = eff_board.get_piece(pos)
                
                if piece is None or piece.color != color:
                    continue
                
                # Check if the piece is in a state where it can initiate a move (e.g. not currently moving/jumping)
                if not piece.can_move():
                    continue
                
                validator = self._move_validator_factory.get_validator(piece.piece_type)
                
                # Check all possible destinations for this piece
                for tr in range(eff_board.rows):
                    for tc in range(eff_board.cols):
                        target = Position(tr, tc)
                        
                        if not validator.is_legal(pos, target, color, eff_board.rows):
                            continue
                            
                        if not self._path_checker.is_path_clear(eff_board, pos, target):
                            continue
                            
                        if not self._path_checker.can_land(eff_board, piece, pos, target, en_passant_targets):
                            continue
                            
                        # Simulate the move to see if it resolves/prevents king threat
                        original_target_piece = eff_board.get_piece(target)
                        eff_board.set_piece(pos, None)
                        eff_board.set_piece(target, piece)
                        
                        is_threatened = self._threat_validator.is_king_threatened(eff_board, color)
                        
                        # Revert the simulation
                        eff_board.set_piece(pos, piece)
                        eff_board.set_piece(target, original_target_piece)
                        
                        if not is_threatened:
                            return True  # Found at least one legal move that leaves the king safe
                            
        return False

    def _has_king(self, board: BoardInterface, color: str) -> bool:
        for r in range(board.rows):
            for c in range(board.cols):
                pos = Position(r, c)
                piece = board.get_piece(pos)
                if piece is not None and piece.piece_type in self._config.king_pieces and piece.color == color:
                    return True
        return False

    def is_checkmate(self, board: BoardInterface, state: GameState, color: str) -> bool:
        """Return True if the specified player is in checkmate."""
        if not self._has_king(board, color):
            return False
        if any(mov.piece.color == color for mov in state.active_movements):
            return False
        if any(cd.piece.color == color for cd in state.active_cooldowns):
            return False

        eff_board = self._movement_manager.get_effective_board(board, state, state.clock_ms)
        if not self._threat_validator.is_king_threatened(eff_board, color):
            return False
            
        return not self._has_any_legal_move(board, state, color)
        
    def is_stalemate(self, board: BoardInterface, state: GameState, color: str) -> bool:
        """Return True if the specified player is in stalemate."""
        if not self._has_king(board, color):
            return False
        if any(mov.piece.color == color for mov in state.active_movements):
            return False
        if any(cd.piece.color == color for cd in state.active_cooldowns):
            return False

        eff_board = self._movement_manager.get_effective_board(board, state, state.clock_ms)
        if self._threat_validator.is_king_threatened(eff_board, color):
            return False
            
        return not self._has_any_legal_move(board, state, color)

    def is_insufficient_material(self, board: BoardInterface) -> bool:
        """Return True if neither player has enough pieces to force checkmate."""
        if not self._has_king(board, "w") or not self._has_king(board, "b"):
            return False
            
        # Count all pieces on the board
        white_pieces = []
        black_pieces = []
        for r in range(board.rows):
            for c in range(board.cols):
                pos = Position(r, c)
                p = board.get_piece(pos)
                if p is not None:
                    if p.color == "w":
                        white_pieces.append((pos, p.piece_type))
                    else:
                        black_pieces.append((pos, p.piece_type))
                        
        # If there are pawns, rooks, or queens, it is sufficient material
        all_types = [pt for _, pt in white_pieces + black_pieces]
        if any(pt in ["P", "R", "Q"] for pt in all_types):
            return False
            
        # Remove kings for calculation
        white_non_king = [x for x in white_pieces if x[1] != "K"]
        black_non_king = [x for x in black_pieces if x[1] != "K"]
        
        total_non_king = len(white_non_king) + len(black_non_king)
        
        # King vs King
        if total_non_king == 0:
            return True
            
        # King + Bishop vs King or King + Knight vs King
        if total_non_king == 1:
            return True
            
        # King + Bishop vs King + Bishop (same color squares)
        if total_non_king == 2:
            if len(white_non_king) == 1 and len(black_non_king) == 1:
                w_pos, w_type = white_non_king[0]
                b_pos, b_type = black_non_king[0]
                if w_type == "B" and b_type == "B":
                    # Check if they are on same color squares
                    if (w_pos.row + w_pos.col) % 2 == (b_pos.row + b_pos.col) % 2:
                        return True
                        
        return False

    def is_threefold_repetition(self, board: BoardInterface, state: GameState) -> bool:
        """Return True if the current position has occurred at least three times."""
        if not self._has_king(board, "w") or not self._has_king(board, "b"):
            return False
            
        current_serialized = serialize_board_state(board, state)
        count = state.position_history.count(current_serialized)
        return count >= 3

    def is_fifty_move_rule(self, board: BoardInterface, state: GameState) -> bool:
        """Return True if no pawn has been moved and no piece has been captured in the last 50 moves."""
        if not self._has_king(board, "w") or not self._has_king(board, "b"):
            return False
        return state.halfmove_clock >= 100
