"""Rule engine — read-only legality validation (Layer 3).

Contains:
  - PathChecker         — path-blocking and capture legality
  - ThreatValidator     — king-in-check detection
  - EndgameValidator    — checkmate, stalemate, insufficient material, repetition, 50-move rule
  - serialize_board_state — helper for repetition detection

Must not own: board mutation, animation, click interpretation, game-over state transitions.
"""

from typing import FrozenSet, List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.model.game_state import GameState
from kungfu_chess.rules.piece_rules import MoveValidatorFactoryInterface


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sign(n: int) -> int:
    """Return -1, 0, or +1 — the sign of *n*."""
    if n > 0:
        return 1
    if n < 0:
        return -1
    return 0


# ---------------------------------------------------------------------------
# PathChecker (Layer 3)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# ThreatValidator (Layer 3)
# ---------------------------------------------------------------------------

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

    def is_king_threatened(self, board: BoardInterface, color: str) -> bool:
        """Return True if the king of *color* is threatened by any enemy piece on *board*."""
        king_pos: Optional[Position] = None
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
            return False

        for r in range(board.rows):
            for c in range(board.cols):
                enemy_pos = Position(r, c)
                enemy_piece = board.get_piece(enemy_pos)
                if enemy_piece is None or enemy_piece.color == color:
                    continue
                validator = self._move_validator_factory.get_validator(enemy_piece.piece_type)
                if not validator.is_legal(enemy_pos, king_pos, enemy_piece.color, board.rows):
                    continue
                if (self._path_checker.is_path_clear(board, enemy_pos, king_pos)
                        and self._path_checker.can_land(board, enemy_piece, enemy_pos, king_pos)):
                    return True
        return False


# ---------------------------------------------------------------------------
# Board-state serialisation (for repetition detection)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# EndgameValidator (Layer 3 — read-only)
# ---------------------------------------------------------------------------

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
        for r in range(board.rows):
            for c in range(board.cols):
                pos = Position(r, c)
                piece = board.get_piece(pos)
                if piece is not None and piece.piece_type in self._config.king_pieces and piece.color == color:
                    return True
        return False

    def _has_any_legal_move(self, board: BoardInterface, state: GameState, color: str) -> bool:
        eff_board = self._movement_manager.get_effective_board(board, state, state.clock_ms)
        en_passant_targets = [ep.pos for ep in state.en_passant_targets]

        def iter_targets(pos: Position, pt: str, rows: int, cols: int):
            if pt == "K":
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        if dr == 0 and dc == 0: continue
                        if 0 <= pos.row + dr < rows and 0 <= pos.col + dc < cols:
                            yield Position(pos.row + dr, pos.col + dc)
            elif pt == "N":
                for dr, dc in ((-2,-1), (-2,1), (-1,-2), (-1,2), (1,-2), (1,2), (2,-1), (2,1)):
                    if 0 <= pos.row + dr < rows and 0 <= pos.col + dc < cols:
                        yield Position(pos.row + dr, pos.col + dc)
            elif pt == "P":
                player_config = self._config.get_player(color)
                dir = player_config.forward_direction if player_config else 1
                for dr in (dir, dir * 2):
                    for dc in (-1, 0, 1):
                        if 0 <= pos.row + dr < rows and 0 <= pos.col + dc < cols:
                            yield Position(pos.row + dr, pos.col + dc)
            elif pt in ("R", "B", "Q"):
                dirs = []
                if pt in ("R", "Q"): dirs.extend([(-1,0), (1,0), (0,-1), (0,1)])
                if pt in ("B", "Q"): dirs.extend([(-1,-1), (-1,1), (1,-1), (1,1)])
                for dr, dc in dirs:
                    for step in range(1, max(rows, cols)):
                        r, c = pos.row + dr * step, pos.col + dc * step
                        if 0 <= r < rows and 0 <= c < cols:
                            yield Position(r, c)
                        else:
                            break
            else:
                for tr in range(rows):
                    for tc in range(cols):
                        if tr != pos.row or tc != pos.col:
                            yield Position(tr, tc)

        for r in range(eff_board.rows):
            for c in range(eff_board.cols):
                pos = Position(r, c)
                piece = eff_board.get_piece(pos)
                if piece is None or piece.color != color:
                    continue
                if not piece.can_move():
                    continue
                validator = self._move_validator_factory.get_validator(piece.piece_type)
                for target in iter_targets(pos, piece.piece_type, eff_board.rows, eff_board.cols):
                    if not validator.is_legal(pos, target, color, eff_board.rows):
                        continue
                    if not self._path_checker.is_path_clear(eff_board, pos, target):
                        continue
                    if not self._path_checker.can_land(eff_board, piece, pos, target, en_passant_targets):
                        continue
                    # Simulate move
                    original_target_piece = eff_board.get_piece(target)
                    eff_board.set_piece(pos, None)
                    eff_board.set_piece(target, piece)
                    is_threatened = self._threat_validator.is_king_threatened(eff_board, color)
                    eff_board.set_piece(pos, piece)
                    eff_board.set_piece(target, original_target_piece)
                    if not is_threatened:
                        return True
        return False

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

    def is_insufficient_material(self, board: BoardInterface) -> bool:
        """Return True if neither player has sufficient material to force checkmate."""
        if not self._has_king(board, "w") or not self._has_king(board, "b"):
            return False

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

        all_types = [pt for _, pt in white_pieces + black_pieces]
        if any(pt in ("P", "R", "Q") for pt in all_types):
            return False

        white_non_king = [(pos, pt) for pos, pt in white_pieces if pt != "K"]
        black_non_king = [(pos, pt) for pos, pt in black_pieces if pt != "K"]
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
