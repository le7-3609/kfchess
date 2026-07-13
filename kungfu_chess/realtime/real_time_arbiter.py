"""Real-time arbiter — movement over time (Layer 4).

Responsibilities:
  - Manage active Motion objects.
  - Simulate time advancement.
  - Resolve arrivals (including capture events and king-capture reporting).
  - Detect same-square and crossing collisions between moving pieces.

Must not own: chess legality (validator calls stay in engine/controller),
clicks, rendering, or script parsing.
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.model.game_state import GameState, Movement, Cooldown, EnPassantTarget
from kungfu_chess.rules.rule_engine import PathCheckerInterface
from kungfu_chess.rules.piece_rules import PromotionStrategyInterface
from kungfu_chess.realtime.duration_strategies import (
    MovementDurationInterface, InstantMovementDuration, ChebyshevDistanceDuration,
)
from kungfu_chess.realtime.proxy_board import ProxyBoard


# ---------------------------------------------------------------------------
# RealTimeArbiter interface
# ---------------------------------------------------------------------------

class RealTimeArbiterInterface(ABC):
    """Abstract contract for the real-time arbiter."""

    @abstractmethod
    def calculate_arrival(self, frm: Position, to: Position, piece: PieceInterface, start_ms: int) -> int:
        """Return the arrival timestamp in milliseconds."""

    @abstractmethod
    def get_position_at(self, mov: Movement, t: int) -> Position:
        """Return the interpolated board position of *mov* at time *t*."""

    @abstractmethod
    def resolve_movements(self, board: BoardInterface, state: GameState, current_ms: int) -> None:
        """Update *board* with any pieces that have finished transit by *current_ms*."""

    @abstractmethod
    def get_effective_board(self, board: BoardInterface, state: GameState, t: int) -> BoardInterface:
        """Return a BoardInterface showing all piece locations at time *t*."""

    @abstractmethod
    def get_valid_en_passant_positions(
        self,
        board: BoardInterface,
        state: GameState,
        color: str,
        t: int
    ) -> List[Position]:
        """Return a list of valid en-passant target positions for a player of *color* at time *t*."""


# ---------------------------------------------------------------------------
# Concrete implementation
# ---------------------------------------------------------------------------

class RealTimeArbiter(RealTimeArbiterInterface):
    """Manages active motions, calculates arrival times, and resolves arrivals.

    Handles:
      - Same-square collisions
      - Crossing (swap-path) collisions
      - Airborne (jumping) piece captures
      - Friendly-fire abort with cooldown
      - En-passant target creation and capture
      - Pawn promotion (delegated to promotion_strategy)
      - King-capture detection (sets game_over)
      - Halfmove clock updates for the 50-move rule
    """

    def __init__(
        self,
        duration_strategy: MovementDurationInterface,
        path_checker: PathCheckerInterface,
        config: 'GameConfig',  # type: ignore[name-defined]
        promotion_strategy: Optional[PromotionStrategyInterface] = None,
        move_event_publisher: Optional['MoveEventPublisher'] = None,  # type: ignore[name-defined]
    ) -> None:
        self._duration_strategy = duration_strategy
        self._path_checker = path_checker
        self._config = config
        self._promotion_strategy = promotion_strategy
        self._move_event_publisher = move_event_publisher

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def calculate_arrival(self, frm: Position, to: Position, piece: PieceInterface, start_ms: int) -> int:
        duration = self._duration_strategy.calculate_duration(frm, to, piece)
        return start_ms + duration

    def get_position_at(self, mov: Movement, t: int) -> Position:
        """Interpolate the board square occupied by *mov* at time *t*."""
        if t <= mov.start_ms:
            return mov.frm
        if t >= mov.arrival_ms:
            return mov.to
        if mov.piece.piece_type in self._config.jumper_pieces:
            return mov.frm  # Knight is airborne — stays at origin until landing

        dist = max(abs(mov.to.row - mov.frm.row), abs(mov.to.col - mov.frm.col))
        if dist == 0:
            return mov.frm
        # Guard: never divide by zero
        ms_per_square = max(1, (mov.arrival_ms - mov.start_ms) // dist)
        step = (t - mov.start_ms) // ms_per_square
        if step >= dist:
            return mov.to

        r_step = (mov.to.row - mov.frm.row) // dist
        c_step = (mov.to.col - mov.frm.col) // dist
        return Position(mov.frm.row + step * r_step, mov.frm.col + step * c_step)

    def get_effective_board(self, board: BoardInterface, state: GameState, t: int) -> BoardInterface:
        """Return a proxy board showing all piece positions at time *t*."""
        return self._build_proxy(board, state, t)

    def get_valid_en_passant_positions(
        self,
        board: BoardInterface,
        state: GameState,
        color: str,
        t: int
    ) -> List[Position]:
        eff_board = self.get_effective_board(board, state, t)
        valid = []
        for ep in state.en_passant_targets:
            p = eff_board.get_piece(ep.capture_pos)
            if p is not None and p.piece_type == "P" and p.color != color:
                valid.append(ep.pos)
        return valid

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_proxy(
        self,
        board: BoardInterface,
        state: GameState,
        t: int,
        exclude_mov: Optional[Movement] = None,
    ) -> BoardInterface:
        return ProxyBoard(board, state.active_movements, t, self.get_position_at, exclude_mov)

    # ------------------------------------------------------------------
    # Arrival resolution
    # ------------------------------------------------------------------

    def resolve_movements(self, board: BoardInterface, state: GameState, current_ms: int) -> None:
        """Advance the simulation to *current_ms*, resolving all arrivals and collisions."""
        active = list(state.active_movements)
        active_cooldowns = list(state.active_cooldowns)
        if not active and not active_cooldowns:
            return

        # Clean up expired en-passant targets.
        state.en_passant_targets = [ep for ep in state.en_passant_targets if ep.expires_ms > current_ms]

        sorted_times = self._collect_event_times(active, active_cooldowns, current_ms)

        t_prev: Optional[int] = None
        for t in sorted_times:
            self._expire_cooldowns_at(state, t)

            current_active = [mov for mov in state.active_movements if mov.start_ms <= t]
            if not current_active:
                t_prev = t
                continue

            positions = self._positions_at(current_active, t)
            reset_halfmove = self._resolve_collisions(board, state, current_active, positions, t, t_prev)

            arrivals_reset, arrivals_increment = self._resolve_arrivals(board, state, t)
            reset_halfmove = reset_halfmove or arrivals_reset

            self._cancel_blocked_ongoing_movements(board, state, t)

            if reset_halfmove:
                state.halfmove_clock = 0
            elif arrivals_increment:
                state.halfmove_clock += 1

            t_prev = t

        # Expire any cooldowns that should have ended by current_ms.
        self._expire_cooldowns_le(state, current_ms)

    # ------------------------------------------------------------------
    # resolve_movements — per-tick phases
    # ------------------------------------------------------------------

    def _collect_event_times(self, active: List[Movement], active_cooldowns: List[Cooldown], current_ms: int) -> list:
        """Collect all discrete event times (movement start/step/arrival, cooldown end) up to current_ms."""
        event_times: set = set()
        for mov in active:
            dist = max(abs(mov.to.row - mov.frm.row), abs(mov.to.col - mov.frm.col))
            event_times.add(mov.start_ms)
            event_times.add(mov.arrival_ms)
            if dist > 1 and mov.piece.piece_type not in self._config.jumper_pieces:
                ms_per_sq = max(1, (mov.arrival_ms - mov.start_ms) // dist)
                for k in range(1, dist):
                    event_times.add(mov.start_ms + k * ms_per_sq)

        for cooldown in active_cooldowns:
            event_times.add(cooldown.end_ms)

        return sorted(t for t in event_times if t <= current_ms)

    def _expire_cooldowns_at(self, state: GameState, t: int) -> None:
        expiring = [c for c in state.active_cooldowns if c.end_ms == t]
        for c in expiring:
            c.piece.transition_to_idle()
            state.active_cooldowns.remove(c)

    def _positions_at(self, current_active: List[Movement], t: int) -> dict:
        return {id(mov): self.get_position_at(mov, t) for mov in current_active}

    def _resolve_collisions(
        self,
        board: BoardInterface,
        state: GameState,
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
                    pos1_prev = self.get_position_at(mov1, t_prev)
                    pos2_prev = self.get_position_at(mov2, t_prev)
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
                    # Determine which movement reached the square earlier vs. later.
                    if mov1.start_ms < mov2.start_ms:
                        early, late = mov1, mov2
                    elif mov2.start_ms < mov1.start_ms:
                        early, late = mov2, mov1
                    else:
                        idx1 = state.active_movements.index(mov1)
                        idx2 = state.active_movements.index(mov2)
                        early, late = (mov1, mov2) if idx1 < idx2 else (mov2, mov1)

                    if early.piece.color != late.piece.color:
                        # Enemy collision — the later arrival eats the earlier one.
                        winner, loser = late, early
                    else:
                        # Friendly collision — the later arrival is stuck in place.
                        winner, loser = early, late

                aborted_or_captured.add(id(loser))

                if winner.piece.color != loser.piece.color:
                    # Enemy collision — loser is captured.
                    board.set_piece(loser.frm, None)
                    loser.piece.transition_to_idle()
                    if state.selected_pos == loser.frm:
                        state.selected_pos = None
                    if loser.piece.piece_type in self._config.king_pieces:
                        state.game_over = True
                        state.game_over_reason = "king_captured"
                    reset_halfmove = True
                else:
                    # Friendly collision — loser's move is aborted, piece enters cooldown.
                    loser.piece.transition_to_cooldown()
                    state.active_cooldowns.append(
                        Cooldown(piece=loser.piece, end_ms=t + self._config.cooldown_duration_ms)
                    )
                    if state.selected_pos == loser.frm:
                        state.selected_pos = None

        # Remove aborted/captured movements.
        for mov in list(state.active_movements):
            if id(mov) in aborted_or_captured:
                state.active_movements.remove(mov)

        return reset_halfmove

    def _resolve_arrivals(self, board: BoardInterface, state: GameState, t: int) -> Tuple[bool, bool]:
        """Resolve every movement arriving at *t*. Returns (reset_halfmove, increment_halfmove)."""
        reset_halfmove = False
        increment_halfmove = False

        arriving = [mov for mov in state.active_movements if mov.arrival_ms == t]
        for mov in arriving:
            frm_still_mine = (board.get_piece(mov.frm) == mov.piece)

            if mov.frm == mov.to:
                # Successful jump-in-place landing.
                if self._resolve_jump_landing(state, mov, t):
                    reset_halfmove = True
            else:
                airborne_enemy_jump = self._find_airborne_enemy_capturing(state, mov, t)

                if airborne_enemy_jump is not None:
                    if self._resolve_airborne_capture(board, state, mov, t, frm_still_mine):
                        reset_halfmove = True
                else:
                    arr_reset, arr_increment = self._resolve_normal_arrival(board, state, mov, t, frm_still_mine, arriving)
                    reset_halfmove = reset_halfmove or arr_reset
                    increment_halfmove = increment_halfmove or arr_increment

            if mov in state.active_movements:
                state.active_movements.remove(mov)

        return reset_halfmove, increment_halfmove

    def _resolve_jump_landing(self, state: GameState, mov: Movement, t: int) -> bool:
        """Successful jump-in-place landing. Returns whether to reset the halfmove clock."""
        mov.piece.transition_to_cooldown()
        state.active_cooldowns.append(
            Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms)
        )
        return mov.piece.piece_type == "P"

    def _find_airborne_enemy_capturing(self, state: GameState, mov: Movement, t: int) -> Optional[Movement]:
        """Return the airborne (jumping) enemy Movement occupying mov's destination, if any."""
        for active_mov in state.active_movements:
            if (active_mov.frm == active_mov.to
                    and active_mov.frm == mov.to
                    and active_mov.start_ms <= t <= active_mov.arrival_ms
                    and active_mov.piece.color != mov.piece.color):
                return active_mov
        return None

    def _resolve_airborne_capture(
        self, board: BoardInterface, state: GameState, mov: Movement, t: int, frm_still_mine: bool
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
        return True

    def _resolve_normal_arrival(
        self,
        board: BoardInterface,
        state: GameState,
        mov: Movement,
        t: int,
        frm_still_mine: bool,
        arriving: List[Movement],
    ) -> Tuple[bool, bool]:
        eff_board = self._build_proxy(board, state, t, exclude_mov=mov)
        path_clear = self._path_checker.is_path_clear(eff_board, mov.frm, mov.to)
        ep_targets = self.get_valid_en_passant_positions(board, state, mov.piece.color, t)
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
        is_capture = self._apply_capture_at_destination(board, state, mov, t, arriving)

        if frm_still_mine:
            board.set_piece(mov.frm, None)
        board.set_piece(mov.to, mov.piece)

        is_ep = self._apply_en_passant_capture(board, state, mov, arriving)
        self._maybe_create_en_passant_target(state, mov, t)

        return self._finalize_arrival_cooldown_and_event(state, mov, t, is_capture, is_ep)

    def _apply_capture_at_destination(
        self, board: BoardInterface, state: GameState, mov: Movement, t: int, arriving: List[Movement]
    ) -> bool:
        target_piece = board.get_piece(mov.to)
        is_capture = False
        if target_piece is not None:
            is_in_flight_future = any(
                am.piece == target_piece and am.arrival_ms > t
                for am in state.active_movements
            )
            if not is_in_flight_future:
                is_capture = (target_piece.color != mov.piece.color)
                if state.selected_pos == mov.to:
                    state.selected_pos = None
                if target_piece.piece_type in self._config.king_pieces:
                    state.game_over = True
                    state.game_over_reason = "king_captured"
                target_piece.transition_to_idle()
                for am in list(state.active_movements):
                    if am.piece == target_piece:
                        if am in arriving:
                            arriving.remove(am)
                        state.active_movements.remove(am)
                for cd in list(state.active_cooldowns):
                    if cd.piece == target_piece:
                        state.active_cooldowns.remove(cd)
        return is_capture

    def _apply_en_passant_capture(
        self, board: BoardInterface, state: GameState, mov: Movement, arriving: List[Movement]
    ) -> bool:
        is_ep = False
        if mov.piece.piece_type == "P":
            for ep in list(state.en_passant_targets):
                if ep.pos == mov.to:
                    captured_piece = board.get_piece(ep.capture_pos)
                    if captured_piece:
                        captured_piece.transition_to_idle()
                        # Clean up movements and cooldowns for the captured piece
                        for am in list(state.active_movements):
                            if am.piece == captured_piece:
                                if am in arriving:
                                    arriving.remove(am)
                                state.active_movements.remove(am)
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
        self, state: GameState, mov: Movement, t: int, is_capture: bool, is_ep: bool
    ) -> Tuple[bool, bool]:
        # Promotion.
        if self._promotion_strategy:
            self._promotion_strategy.evaluate_promotion(mov.piece, mov.to, self._config)

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

    def _cancel_blocked_ongoing_movements(self, board: BoardInterface, state: GameState, t: int) -> None:
        """Cancel movements still in flight at *t* whose path/landing has since become invalid."""
        ongoing = [mov for mov in state.active_movements if mov.start_ms <= t and mov.arrival_ms > t]
        for mov in ongoing:
            frm_still_mine = (board.get_piece(mov.frm) == mov.piece)
            eff_board = self._build_proxy(board, state, t, exclude_mov=mov)
            path_clear = self._path_checker.is_path_clear(eff_board, mov.frm, mov.to)
            ep_targets = self.get_valid_en_passant_positions(board, state, mov.piece.color, t)
            can_land = self._path_checker.can_land(eff_board, mov.piece, mov.frm, mov.to, ep_targets)

            if not path_clear or not can_land:
                if frm_still_mine:
                    mov.piece.transition_to_cooldown()
                    state.active_cooldowns.append(
                        Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms)
                    )
                else:
                    mov.piece.transition_to_idle()

                state.active_movements.remove(mov)

    def _expire_cooldowns_le(self, state: GameState, current_ms: int) -> None:
        expiring_cooldowns = [c for c in state.active_cooldowns if c.end_ms <= current_ms]
        for c in expiring_cooldowns:
            c.piece.transition_to_idle()
            state.active_cooldowns.remove(c)
