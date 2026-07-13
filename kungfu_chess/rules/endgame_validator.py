"""Endgame validator — checkmate, stalemate, insufficient material, repetition, 50-move rule (Layer 3).

All checks are read-only: must not own board mutation, animation, click
interpretation, or game-over state transitions.
"""

from typing import List, Tuple

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.model.game_state import GameState
from kungfu_chess.rules.piece_rules import MoveValidatorFactoryInterface
from kungfu_chess.rules.path_checker import PathCheckerInterface
from kungfu_chess.rules.threat_validator import ThreatValidator


def serialize_board_state(board: BoardInterface, state: GameState) -> str:
    """Produce a canonical string representation of the board + relevant state."""
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
            if p is not None and p.piece_type in ("K", "R"):
                castling_list.append(f"{r},{c},{p.color},{p.piece_type},{p.has_moved}")
    castling_part = ";".join(sorted(castling_list))

    return f"{board_part}|{ep_part}|{castling_part}"


class EndgameValidator:
    """Evaluates the board for checkmate, stalemate, insufficient material, repetition, 50-move rule.

    All checks are read-only: this class never mutates the board or game state.
    """

    def __init__(
        self,
        move_validator_factory: MoveValidatorFactoryInterface,
        path_checker: PathCheckerInterface,
        movement_manager: 'MovementManagerInterface',  # type: ignore[name-defined]
        threat_validator: ThreatValidator,
        config: 'GameConfig',  # type: ignore[name-defined]
    ) -> None:
        self._move_validator_factory = move_validator_factory
        self._path_checker = path_checker
        self._movement_manager = movement_manager
        self._threat_validator = threat_validator
        self._config = config

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _has_king(self, board: BoardInterface, color: str) -> bool:
        return self._threat_validator.find_king(board, color) is not None

    def _has_any_legal_move(self, board: BoardInterface, state: GameState, color: str) -> bool:
        eff_board = self._movement_manager.get_effective_board(board, state, state.clock_ms)
        en_passant_targets = self._movement_manager.get_valid_en_passant_positions(board, state, color, state.clock_ms)

        for r in range(eff_board.rows):
            for c in range(eff_board.cols):
                pos = Position(r, c)
                piece = eff_board.get_piece(pos)
                if piece is None or piece.color != color or not piece.can_move():
                    continue
                if self._piece_has_any_legal_move(eff_board, pos, piece, en_passant_targets):
                    return True
        return False

    def _piece_has_any_legal_move(
        self,
        board: BoardInterface,
        pos: Position,
        piece: PieceInterface,
        en_passant_targets: List[Position]
    ) -> bool:
        validator = self._move_validator_factory.get_validator(piece.piece_type)
        candidates = validator.get_candidate_targets(pos, piece.color, board.rows, board.cols)
        for target in candidates:
            if not validator.is_legal(pos, target, piece.color, board.rows):
                continue
            if not self._path_checker.is_path_clear(board, pos, target):
                continue
            if not self._path_checker.can_land(board, piece, pos, target, en_passant_targets):
                continue
            if self._is_legal_move_safe_from_check(board, pos, target, piece):
                return True
        return False

    def _is_legal_move_safe_from_check(
        self,
        board: BoardInterface,
        frm: Position,
        to: Position,
        piece: PieceInterface
    ) -> bool:
        """Simulates the move and checks if it leaves the king threatened."""
        original_target_piece = board.get_piece(to)
        board.set_piece(frm, None)
        board.set_piece(to, piece)
        is_threatened = self._threat_validator.is_king_threatened(board, piece.color)
        board.set_piece(frm, piece)
        board.set_piece(to, original_target_piece)
        return not is_threatened

    # ------------------------------------------------------------------
    # Public checks
    # ------------------------------------------------------------------

    def is_checkmate(self, board: BoardInterface, state: GameState, color: str) -> bool:
        """Return True if *color* is in checkmate."""
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
        """Return True if *color* is in stalemate."""
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

    def _collect_pieces_by_color(self, board: BoardInterface) -> Tuple[list, list]:
        """Return (white_pieces, black_pieces) as lists of (Position, piece_type)."""
        white_pieces: list = []
        black_pieces: list = []
        for r in range(board.rows):
            for c in range(board.cols):
                pos = Position(r, c)
                p = board.get_piece(pos)
                if p is not None:
                    if p.color == "w":
                        white_pieces.append((pos, p.piece_type))
                    else:
                        black_pieces.append((pos, p.piece_type))
        return white_pieces, black_pieces

    def _is_draw_by_material(self, white_non_king: list, black_non_king: list) -> bool:
        total_non_king = len(white_non_king) + len(black_non_king)

        if total_non_king == 0:
            return True
        if total_non_king == 1:
            return True
        if total_non_king == 2 and len(white_non_king) == 1 and len(black_non_king) == 1:
            w_pos, w_type = white_non_king[0]
            b_pos, b_type = black_non_king[0]
            if w_type == "B" and b_type == "B":
                if (w_pos.row + w_pos.col) % 2 == (b_pos.row + b_pos.col) % 2:
                    return True
        return False

    def is_insufficient_material(self, board: BoardInterface) -> bool:
        """Return True if neither player has sufficient material to force checkmate."""
        if not self._has_king(board, "w") or not self._has_king(board, "b"):
            return False

        white_pieces, black_pieces = self._collect_pieces_by_color(board)

        all_types = [pt for _, pt in white_pieces + black_pieces]
        if any(pt in ("P", "R", "Q") for pt in all_types):
            return False

        white_non_king = [(pos, pt) for pos, pt in white_pieces if pt != "K"]
        black_non_king = [(pos, pt) for pos, pt in black_pieces if pt != "K"]
        return self._is_draw_by_material(white_non_king, black_non_king)

    def is_threefold_repetition(self, board: BoardInterface, state: GameState) -> bool:
        """Return True if the current position has occurred at least a configured number of times."""
        if not self._has_king(board, "w") or not self._has_king(board, "b"):
            return False
        current_serialized = serialize_board_state(board, state)
        return state.position_history.count(current_serialized) >= self._config.repetitions_for_draw

    def is_fifty_move_rule(self, board: BoardInterface, state: GameState) -> bool:
        """Return True if the 50-move rule applies (halfmove clock >= threshold)."""
        if not self._has_king(board, "w") or not self._has_king(board, "b"):
            return False
        return state.halfmove_clock >= self._config.halfmoves_for_draw
