"""Collision resolution between concurrently moving pieces (Layer 4).

Detects same-square and crossing (swap-path) collisions among movements
active at a given tick, and applies KungFu Chess's winner/loser rules:
enemy collisions capture, friendly collisions abort the later mover into
cooldown. Castling's simultaneous King+Rook arrival is exempted.
"""

from typing import Callable, List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.game_state import GameState, Movement, Cooldown


class CollisionResolver:
    """Resolves same-square/crossing collisions for a single tick."""

    def __init__(
        self,
        config: 'GameConfig',  # type: ignore[name-defined]
        position_at: Callable[[Movement, int], Position],
    ) -> None:
        self._config = config
        self._position_at = position_at

    def resolve(
        self,
        board: BoardInterface,
        state: GameState,
        movements: List[Movement],
        current_active: List[Movement],
        positions: dict,
        t: int,
        t_prev: Optional[int],
    ) -> bool:
        """Detect and resolve same-square/crossing collisions.

        Returns whether the halfmove clock should reset.
        """
        reset_halfmove = False
        aborted_or_captured: set = set()
        n = len(current_active)
        for i in range(n):
            for j in range(i + 1, n):
                mov1 = current_active[i]
                mov2 = current_active[j]
                if id(mov1) in aborted_or_captured or id(mov2) in aborted_or_captured:
                    continue

                pos1 = positions[id(mov1)]
                pos2 = positions[id(mov2)]

                is_same_sq = (pos1 == pos2)
                is_crossing = False
                if t_prev is not None:
                    pos1_prev = self._position_at(mov1, t_prev)
                    pos2_prev = self._position_at(mov2, t_prev)
                    if pos1 == pos2_prev and pos2 == pos1_prev and pos1 != pos2:
                        is_crossing = True

                if not is_same_sq and not is_crossing:
                    continue

                # Castling exception: friendly King + Rook arriving simultaneously.
                if mov1.piece.color == mov2.piece.color and mov1.arrival_ms == mov2.arrival_ms:
                    if ({mov1.piece.piece_type, mov2.piece.piece_type} == {"K", "R"}):
                        continue

                # Determine winner/loser using KungFu Chess collision rules.
                is_mov1_jump = (mov1.frm == mov1.to)
                is_mov2_jump = (mov2.frm == mov2.to)

                if is_mov1_jump and not is_mov2_jump and mov1.piece.color != mov2.piece.color:
                    winner, loser = mov1, mov2
                elif is_mov2_jump and not is_mov1_jump and mov1.piece.color != mov2.piece.color:
                    winner, loser = mov2, mov1
                else:
                    if mov1.start_ms < mov2.start_ms:
                        early, late = mov1, mov2
                    elif mov2.start_ms < mov1.start_ms:
                        early, late = mov2, mov1
                    else:
                        idx1 = movements.index(mov1)
                        idx2 = movements.index(mov2)
                        early, late = (mov1, mov2) if idx1 < idx2 else (mov2, mov1)

                    if early.piece.color != late.piece.color:
                        # Enemy collision — the later arrival eats the earlier one.
                        winner, loser = late, early
                    else:
                        # Friendly collision — the later arrival is stuck in place.
                        winner, loser = early, late

                aborted_or_captured.add(id(loser))

                if winner.piece.color != loser.piece.color:
                    board.set_piece(loser.frm, None)
                    loser.piece.transition_to_idle()
                    if state.selected_pos == loser.frm:
                        state.selected_pos = None
                    if loser.piece.piece_type in self._config.king_pieces:
                        state.game_over = True
                        state.game_over_reason = "king_captured"
                    reset_halfmove = True
                else:
                    # Friendly collision — loser's move is aborted, piece enters cooldown
                    # wherever it had actually reached when the collision happened.
                    stuck_pos = positions[id(loser)]
                    if board.get_piece(loser.frm) is loser.piece and stuck_pos != loser.frm:
                        board.set_piece(loser.frm, None)
                        board.set_piece(stuck_pos, loser.piece)
                    loser.piece.transition_to_cooldown()
                    state.active_cooldowns.append(
                        Cooldown(piece=loser.piece, end_ms=t + self._config.cooldown_duration_ms)
                    )
                    if state.selected_pos == loser.frm:
                        state.selected_pos = None

        for mov in list(movements):
            if id(mov) in aborted_or_captured:
                movements.remove(mov)

        return reset_halfmove
