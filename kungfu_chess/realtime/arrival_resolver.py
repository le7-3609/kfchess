"""Arrival resolution for movements completing transit (Layer 4).

Handles what happens when a Movement reaches its arrival time: jump-in-place
landings, airborne (jumping) piece captures, normal arrivals (capture,
en-passant, promotion, cooldown/event bookkeeping), and re-validating
still-in-flight movements whose path/landing has since become blocked.
"""

from typing import List, Optional, Tuple

from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.game_state import GameState, Movement, Cooldown, EnPassantTarget
from kungfu_chess.model.position import Position
from kungfu_chess.rules.rule_engine import PathCheckerInterface
from kungfu_chess.rules.piece_rules import PromotionStrategyInterface
from kungfu_chess.realtime.arbiter_interfaces import RealTimeArbiterInterface


class ArrivalResolver:
    """Resolves movements arriving at the current tick, and cancels blocked ones."""

    def __init__(
        self,
        path_checker: PathCheckerInterface,
        config: 'GameConfig',  # type: ignore[name-defined]
        promotion_strategy: Optional[PromotionStrategyInterface] = None,
        move_event_publisher: Optional['MoveEventPublisher'] = None,  # type: ignore[name-defined]
    ) -> None:
        self._path_checker = path_checker
        self._config = config
        self._promotion_strategy = promotion_strategy
        self._move_event_publisher = move_event_publisher

    # ------------------------------------------------------------------
    # Arrival resolution
    # ------------------------------------------------------------------

    def resolve_arrivals(
        self,
        board: BoardInterface,
        state: GameState,
        movements: List[Movement],
        t: int,
        arbiter: RealTimeArbiterInterface,
    ) -> Tuple[bool, bool]:
        """Resolve every movement arriving at *t*. Returns (reset_halfmove, increment_halfmove)."""
        reset_halfmove = False
        increment_halfmove = False

        arriving = [mov for mov in movements if mov.arrival_ms == t]
        for mov in arriving:
            frm_still_mine = (board.get_piece(mov.frm) == mov.piece)

            if mov.frm == mov.to:
                # Successful jump-in-place landing.
                if self._resolve_jump_landing(state, mov, t):
                    reset_halfmove = True
            else:
                airborne_enemy_jump = self._find_airborne_enemy_capturing(movements, mov, t)

                if airborne_enemy_jump is not None:
                    if self._resolve_airborne_capture(board, state, mov, airborne_enemy_jump, t, frm_still_mine):
                        reset_halfmove = True
                else:
                    arr_reset, arr_increment = self._resolve_normal_arrival(
                        board, state, mov, t, frm_still_mine, arriving, movements, arbiter
                    )
                    reset_halfmove = reset_halfmove or arr_reset
                    increment_halfmove = increment_halfmove or arr_increment

            if mov in movements:
                movements.remove(mov)

        return reset_halfmove, increment_halfmove

    def _resolve_jump_landing(self, state: GameState, mov: Movement, t: int) -> bool:
        """Successful jump-in-place landing. Returns whether to reset the halfmove clock."""
        mov.piece.transition_to_cooldown()
        state.active_cooldowns.append(
            Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms)
        )
        return mov.piece.piece_type == "P"

    def _find_airborne_enemy_capturing(self, movements: List[Movement], mov: Movement, t: int) -> Optional[Movement]:
        """Return the airborne (jumping) enemy Movement occupying mov's destination, if any."""
        for active_mov in movements:
            if (active_mov.frm == active_mov.to
                    and active_mov.frm == mov.to
                    and active_mov.start_ms <= t <= active_mov.arrival_ms
                    and active_mov.piece.color != mov.piece.color):
                return active_mov
        return None

    def _resolve_airborne_capture(
        self, board: BoardInterface, state: GameState, mov: Movement, airborne_mov: Movement, t: int, frm_still_mine: bool
    ) -> bool:
        """Airborne piece captures the arriving enemy. Returns whether to reset the halfmove clock."""
        if frm_still_mine:
            board.set_piece(mov.frm, None)
        mov.piece.transition_to_idle()
        if state.selected_pos == mov.frm:
            state.selected_pos = None
        if mov.piece.piece_type in self._config.king_pieces:
            state.game_over = True
            state.game_over_reason = "king_captured"
            state.winner = airborne_mov.piece.color
        return True

    def _resolve_normal_arrival(
        self,
        board: BoardInterface,
        state: GameState,
        mov: Movement,
        t: int,
        frm_still_mine: bool,
        arriving: List[Movement],
        movements: List[Movement],
        arbiter: RealTimeArbiterInterface,
    ) -> Tuple[bool, bool]:
        eff_board = arbiter.get_effective_board(board, state, t, exclude_mov=mov)
        path_clear = self._path_checker.is_path_clear(eff_board, mov.frm, mov.to)
        ep_targets = arbiter.get_valid_en_passant_positions(board, state, mov.piece.color, t)
        can_land = self._path_checker.can_land(eff_board, mov.piece, mov.frm, mov.to, ep_targets)

        if not (path_clear and can_land):
            # Aborted: path blocked or landing invalid.
            if frm_still_mine:
                mov.piece.transition_to_cooldown()
                state.active_cooldowns.append(
                    Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms)
                )
            else:
                # Origin taken — piece is eliminated.
                mov.piece.transition_to_idle()
            return False, False

        # Successful arrival!
        is_capture = self._apply_capture_at_destination(board, state, mov, t, arriving, movements)

        if frm_still_mine:
            board.set_piece(mov.frm, None)
        board.set_piece(mov.to, mov.piece)

        is_ep = self._apply_en_passant_capture(board, state, mov, arriving, movements)
        self._maybe_create_en_passant_target(state, mov, t)

        return self._finalize_arrival_cooldown_and_event(board, state, mov, t, is_capture, is_ep)

    def _apply_capture_at_destination(
        self,
        board: BoardInterface,
        state: GameState,
        mov: Movement,
        t: int,
        arriving: List[Movement],
        movements: List[Movement],
    ) -> bool:
        target_piece = board.get_piece(mov.to)
        is_capture = False
        if target_piece is not None:
            is_in_flight_future = any(
                am.piece == target_piece and am.arrival_ms > t
                for am in movements
            )
            if not is_in_flight_future:
                is_capture = (target_piece.color != mov.piece.color)
                if state.selected_pos == mov.to:
                    state.selected_pos = None
                if target_piece.piece_type in self._config.king_pieces:
                    state.game_over = True
                    state.game_over_reason = "king_captured"
                    state.winner = mov.piece.color
                target_piece.transition_to_idle()
                for am in list(movements):
                    if am.piece == target_piece:
                        if am in arriving:
                            arriving.remove(am)
                        movements.remove(am)
                for cd in list(state.active_cooldowns):
                    if cd.piece == target_piece:
                        state.active_cooldowns.remove(cd)
        return is_capture

    def _apply_en_passant_capture(
        self,
        board: BoardInterface,
        state: GameState,
        mov: Movement,
        arriving: List[Movement],
        movements: List[Movement],
    ) -> bool:
        is_ep = False
        if mov.piece.piece_type == "P":
            for ep in list(state.en_passant_targets):
                if ep.pos == mov.to:
                    captured_piece = board.get_piece(ep.capture_pos)
                    if captured_piece:
                        captured_piece.transition_to_idle()
                        # Clean up movements and cooldowns for the captured piece
                        for am in list(movements):
                            if am.piece == captured_piece:
                                if am in arriving:
                                    arriving.remove(am)
                                movements.remove(am)
                        for cd in list(state.active_cooldowns):
                            if cd.piece == captured_piece:
                                state.active_cooldowns.remove(cd)
                    board.set_piece(ep.capture_pos, None)
                    state.en_passant_targets.remove(ep)
                    is_ep = True
                    break
        return is_ep

    def _maybe_create_en_passant_target(self, state: GameState, mov: Movement, t: int) -> None:
        if mov.piece.piece_type == "P":
            player_cfg = self._config.get_player(mov.piece.color)
            if player_cfg and abs(mov.to.row - mov.frm.row) == 2 and mov.frm.row in player_cfg.pawn_start_rows:
                ep_pos = Position(mov.frm.row + player_cfg.forward_direction, mov.frm.col)
                state.en_passant_targets.append(
                    EnPassantTarget(
                        pos=ep_pos,
                        capture_pos=mov.to,
                        expires_ms=t + self._config.en_passant_duration_ms,
                    )
                )

    def _finalize_arrival_cooldown_and_event(
        self, board: BoardInterface, state: GameState, mov: Movement, t: int, is_capture: bool, is_ep: bool
    ) -> Tuple[bool, bool]:
        # Promotion: pieces are immutable, so a promotion replaces mov.piece
        # with a newly constructed piece rather than mutating piece_type.
        if self._promotion_strategy:
            promoted = self._promotion_strategy.evaluate_promotion(mov.piece, mov.to, self._config)
            if promoted is not None:
                mov.piece = promoted
                board.set_piece(mov.to, promoted)

        mov.piece.transition_to_cooldown()
        state.active_cooldowns.append(
            Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms)
        )
        if self._move_event_publisher:
            self._move_event_publisher.publish(mov.piece, mov.frm, mov.to)

        reset_halfmove = False
        increment_halfmove = False
        if mov.piece.piece_type == "P" or is_capture or is_ep:
            reset_halfmove = True
        elif mov.frm != mov.to:
            increment_halfmove = True
        return reset_halfmove, increment_halfmove

    # ------------------------------------------------------------------
    # Ongoing-movement re-validation
    # ------------------------------------------------------------------

    def cancel_blocked_ongoing_movements(
        self,
        board: BoardInterface,
        state: GameState,
        movements: List[Movement],
        t: int,
        arbiter: RealTimeArbiterInterface,
    ) -> None:
        """Cancel movements still in flight at *t* whose path/landing has since become invalid."""
        ongoing = [mov for mov in movements if mov.start_ms <= t and mov.arrival_ms > t]
        for mov in ongoing:
            frm_still_mine = (board.get_piece(mov.frm) == mov.piece)
            eff_board = arbiter.get_effective_board(board, state, t, exclude_mov=mov)
            path_clear = self._path_checker.is_path_clear(eff_board, mov.frm, mov.to)
            ep_targets = arbiter.get_valid_en_passant_positions(board, state, mov.piece.color, t)
            can_land = self._path_checker.can_land(eff_board, mov.piece, mov.frm, mov.to, ep_targets)

            if not path_clear or not can_land:
                if frm_still_mine:
                    mov.piece.transition_to_cooldown()
                    state.active_cooldowns.append(
                        Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms)
                    )
                else:
                    mov.piece.transition_to_idle()

                movements.remove(mov)
