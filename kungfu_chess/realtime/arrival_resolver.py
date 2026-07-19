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
from kungfu_chess.events import (
    ABORT_REASON_CAPTURED_IN_FLIGHT,
    ABORT_REASON_PATH_BLOCKED,
    EventBus,
    GameEndedEvent,
    MoveAbortedEvent,
    PieceCapturedEvent,
    PieceMovedEvent,
    PiecePromotedEvent,
)
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
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self._path_checker = path_checker
        self._config = config
        self._promotion_strategy = promotion_strategy
        self._event_bus = event_bus or EventBus()

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
            frm_still_mine = (board.get_piece(mov.frm) is mov.piece)

            if mov.frm == mov.to:
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
        self._enter_cooldown(state, mov.piece, t)
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
        # Reported at mov.to, the square the arriving piece was struck on —
        # it never got to occupy it, but that is where the clash is visible.
        self._announce_capture(state, mov.piece, mov.to, airborne_mov.piece, t)
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
        if not self._can_complete(board, state, mov, t, arbiter):
            self._abort_movement(board, state, mov, t, frm_still_mine, arbiter)
            return False, False

        is_capture = self._apply_capture_at_destination(board, state, mov, t, arriving, movements)

        if frm_still_mine:
            board.set_piece(mov.frm, None)
        board.set_piece(mov.to, mov.piece)

        is_ep = self._apply_en_passant_capture(board, state, mov, t, arriving, movements)
        self._maybe_create_en_passant_target(state, mov, t)

        return self._finalize_arrival_cooldown_and_event(board, state, mov, t, is_capture, is_ep)

    def _can_complete(
        self, board: BoardInterface, state: GameState, mov: Movement, t: int, arbiter: RealTimeArbiterInterface
    ) -> bool:
        """True if *mov*'s path is still clear and its destination still landable at *t*.

        Evaluated against the board as it stands excluding *mov* itself, since a
        piece must not be treated as blocking its own path.
        """
        eff_board = arbiter.get_effective_board(board, state, t, exclude_mov=mov)
        if not self._path_checker.is_path_clear(eff_board, mov.frm, mov.to):
            return False
        ep_targets = arbiter.get_valid_en_passant_positions(board, state, mov.piece.color, t)
        return self._path_checker.can_land(eff_board, mov.piece, mov.frm, mov.to, ep_targets)

    def _abort_movement(
        self,
        board: BoardInterface,
        state: GameState,
        mov: Movement,
        t: int,
        frm_still_mine: bool,
        arbiter: RealTimeArbiterInterface,
    ) -> None:
        """Abandon *mov*, parking its piece where it had actually reached by *t*.

        When *frm_still_mine* is false the origin square has already been taken
        by someone else, meaning this piece was captured mid-flight and has no
        square to return to — it simply leaves play.
        """
        if not frm_still_mine:
            mov.piece.transition_to_idle()
            self._announce_abort(mov, mov.frm, ABORT_REASON_CAPTURED_IN_FLIGHT, t)
            return

        stuck_pos = arbiter.get_stuck_position(mov, t)
        if stuck_pos != mov.frm:
            board.set_piece(mov.frm, None)
            board.set_piece(stuck_pos, mov.piece)
        self._enter_cooldown(state, mov.piece, t)
        self._announce_abort(mov, stuck_pos, ABORT_REASON_PATH_BLOCKED, t)

    def _announce_capture(self, state: GameState, victim, pos: Position, captor, t: int) -> None:
        """Publish *victim*'s capture by *captor*, ending the game if it was a king."""
        self._event_bus.publish(PieceCapturedEvent(
            at_ms=t,
            color=victim.color,
            piece_type=victim.piece_type,
            pos=pos,
            captor_color=captor.color,
            captor_piece_type=captor.piece_type,
        ))
        if victim.piece_type in self._config.king_pieces:
            self._announce_game_end(state, "king_captured", captor.color, t)

    def _announce_abort(self, mov: Movement, stopped_at: Position, reason: str, t: int) -> None:
        self._event_bus.publish(MoveAbortedEvent(
            at_ms=t,
            color=mov.piece.color,
            piece_type=mov.piece.piece_type,
            frm=mov.frm,
            stopped_at=stopped_at,
            reason=reason,
        ))

    def _announce_game_end(self, state: GameState, reason: str, winner: Optional[str], t: int) -> None:
        """End the game and publish it, only for the ending that actually took effect."""
        if state.end_game(reason, winner):
            self._event_bus.publish(GameEndedEvent(at_ms=t, reason=reason, winner=winner))

    def _enter_cooldown(self, state: GameState, piece, t: int) -> None:
        """Put *piece* into cooldown for the configured duration starting at *t*."""
        piece.transition_to_cooldown()
        state.active_cooldowns.append(
            Cooldown(piece=piece, end_ms=t + self._config.cooldown_duration_ms)
        )

    def _remove_piece_from_play(
        self, state: GameState, piece, arriving: List[Movement], movements: List[Movement]
    ) -> None:
        """Drop every pending movement and cooldown belonging to a captured *piece*."""
        piece.transition_to_idle()
        for mov in list(movements):
            if mov.piece == piece:
                if mov in arriving:
                    arriving.remove(mov)
                movements.remove(mov)
        for cooldown in list(state.active_cooldowns):
            if cooldown.piece == piece:
                state.active_cooldowns.remove(cooldown)

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
        if target_piece is None:
            return False
        is_leaving = any(am.piece == target_piece and am.arrival_ms > t for am in movements)
        if is_leaving:
            return False

        if state.selected_pos == mov.to:
            state.selected_pos = None
        self._announce_capture(state, target_piece, mov.to, mov.piece, t)
        self._remove_piece_from_play(state, target_piece, arriving, movements)
        return target_piece.color != mov.piece.color

    def _apply_en_passant_capture(
        self,
        board: BoardInterface,
        state: GameState,
        mov: Movement,
        t: int,
        arriving: List[Movement],
        movements: List[Movement],
    ) -> bool:
        if mov.piece.piece_type != "P":
            return False

        for ep in list(state.en_passant_targets):
            if ep.pos != mov.to:
                continue
            captured_piece = board.get_piece(ep.capture_pos)
            if captured_piece:
                self._announce_capture(state, captured_piece, ep.capture_pos, mov.piece, t)
                self._remove_piece_from_play(state, captured_piece, arriving, movements)
            board.set_piece(ep.capture_pos, None)
            state.en_passant_targets.remove(ep)
            return True
        return False

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
                self._event_bus.publish(PiecePromotedEvent(
                    at_ms=t,
                    color=mov.piece.color,
                    from_piece_type=mov.piece.piece_type,
                    to_piece_type=promoted.piece_type,
                    pos=mov.to,
                ))
                mov.piece = promoted
                board.set_piece(mov.to, promoted)

        self._enter_cooldown(state, mov.piece, t)
        self._event_bus.publish(PieceMovedEvent(
            at_ms=t,
            color=mov.piece.color,
            piece_type=mov.piece.piece_type,
            frm=mov.frm,
            to=mov.to,
            was_capture=is_capture or is_ep,
        ))

        reset_halfmove = False
        increment_halfmove = False
        if mov.piece.piece_type == "P" or is_capture or is_ep:
            reset_halfmove = True
        elif mov.frm != mov.to:
            increment_halfmove = True
        return reset_halfmove, increment_halfmove

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
            if self._is_castling_partner_ongoing(mov, ongoing):
                continue
            if self._can_complete(board, state, mov, t, arbiter):
                continue

            frm_still_mine = (board.get_piece(mov.frm) is mov.piece)
            self._abort_movement(board, state, mov, t, frm_still_mine, arbiter)
            movements.remove(mov)

    def _is_castling_partner_ongoing(self, mov: Movement, ongoing: List[Movement]) -> bool:
        """True if *mov* is one leg of a King+Rook castle whose other leg is still in flight.

        Castling's King and Rook legs necessarily cross/swap paths and briefly
        occupy each other's squares mid-transit; the normal same-color
        path/landing re-validation would otherwise abort both. Mirrors the
        castling exemption in CollisionResolver (same-color K+R pair sharing
        start/arrival times).
        """
        if mov.piece.piece_type not in ("K", "R"):
            return False
        for other in ongoing:
            if other is mov:
                continue
            if (other.piece.color == mov.piece.color
                    and other.start_ms == mov.start_ms
                    and other.arrival_ms == mov.arrival_ms
                    and {other.piece.piece_type, mov.piece.piece_type} == {"K", "R"}):
                return True
        return False
