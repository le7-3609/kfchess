"""RealTimeArbiter — movement-over-time orchestration (Layer 4).

Owns the tick loop: advancing the simulation clock, collecting discrete
event times, and delegating same-tick collision/arrival resolution to
CollisionResolver and ArrivalResolver.

Must not own: chess legality (validator calls stay in engine/controller),
clicks, rendering, or script parsing.
"""

from typing import List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.model.game_state import GameState, Movement, Cooldown
from kungfu_chess.rules.rule_engine import PathCheckerInterface
from kungfu_chess.rules.piece_rules import PromotionStrategyInterface
from kungfu_chess.realtime.arbiter_interfaces import RealTimeArbiterInterface
from kungfu_chess.realtime.duration_strategies import MovementDurationInterface
from kungfu_chess.realtime.proxy_board import ProxyBoard
from kungfu_chess.realtime.collision_resolver import CollisionResolver
from kungfu_chess.realtime.arrival_resolver import ArrivalResolver


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

    Collision detection and arrival resolution are delegated to
    CollisionResolver and ArrivalResolver respectively; this class owns the
    tick loop that sequences them.

    Owns the active-motion collection privately (not stored on GameState).
    Other layers must go through register_motion / has_active_motion /
    movements / remove_motion rather than mutating a shared list.
    """

    def __init__(
        self,
        duration_strategy: MovementDurationInterface,
        path_checker: PathCheckerInterface,
        config: 'GameConfig',  # type: ignore[name-defined]
        promotion_strategy: Optional[PromotionStrategyInterface] = None,
        move_event_publisher: Optional['MoveEventPublisher'] = None,  # type: ignore[name-defined]
        collision_resolver: Optional[CollisionResolver] = None,
        arrival_resolver: Optional[ArrivalResolver] = None,
    ) -> None:
        self._duration_strategy = duration_strategy
        self._path_checker = path_checker
        self._config = config
        self._promotion_strategy = promotion_strategy
        self._move_event_publisher = move_event_publisher
        self._active_movements: List[Movement] = []

        if collision_resolver is None:
            collision_resolver = CollisionResolver(config=config, position_at=self.get_position_at)
        self._collision_resolver = collision_resolver

        if arrival_resolver is None:
            arrival_resolver = ArrivalResolver(
                path_checker=path_checker,
                config=config,
                promotion_strategy=promotion_strategy,
                move_event_publisher=move_event_publisher,
            )
        self._arrival_resolver = arrival_resolver

    def register_motion(self, mov: Movement) -> None:
        self._active_movements.append(mov)

    def remove_motion(self, mov: Movement) -> None:
        if mov in self._active_movements:
            self._active_movements.remove(mov)

    def movements(self) -> List[Movement]:
        return list(self._active_movements)

    def has_active_motion(self, piece: Optional[PieceInterface] = None) -> bool:
        if piece is None:
            return bool(self._active_movements)
        return any(mov.piece == piece for mov in self._active_movements)

    def has_active_motion_for_color(self, color: str) -> bool:
        return any(mov.piece.color == color for mov in self._active_movements)

    def calculate_arrival(self, frm: Position, to: Position, piece: PieceInterface, start_ms: int) -> int:
        duration = self._duration_strategy.calculate_duration(frm, to, piece)
        return start_ms + duration

    def update_preferences(self, ms_per_square: int, cooldown_ms: int) -> None:
        """Apply new movement-speed / cooldown preferences at runtime."""
        self._config.cooldown_duration_ms = cooldown_ms
        self._config.ms_per_square = ms_per_square
        if hasattr(self._duration_strategy, "set_ms_per_square"):
            self._duration_strategy.set_ms_per_square(ms_per_square)

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
        # Floors to 0 for motions faster than 1ms per square, which would make
        # the step division below divide by zero.
        ms_per_square = max(1, (mov.arrival_ms - mov.start_ms) // dist)
        step = (t - mov.start_ms) // ms_per_square
        if step >= dist:
            return mov.to

        r_step = (mov.to.row - mov.frm.row) // dist
        c_step = (mov.to.col - mov.frm.col) // dist
        return Position(mov.frm.row + step * r_step, mov.frm.col + step * c_step)

    def get_stuck_position(self, mov: Movement, t: int) -> Position:
        """Return the square *mov* should be parked at if aborted at time *t*.

        A blocked movement never actually reaches its destination, so it must
        stop one square short of wherever get_position_at(mov, t) would place
        it — that square is the one that turned out to be blocked. Aborting
        mid-flight (t < arrival_ms) already lands on a square the piece
        legitimately passed through, so no step-back is needed there.
        """
        pos = self.get_position_at(mov, t)
        if pos != mov.to or mov.frm == mov.to:
            return pos

        dist = max(abs(mov.to.row - mov.frm.row), abs(mov.to.col - mov.frm.col))
        r_step = (mov.to.row - mov.frm.row) // dist
        c_step = (mov.to.col - mov.frm.col) // dist
        return Position(mov.to.row - r_step, mov.to.col - c_step)

    def get_effective_board(
        self,
        board: BoardInterface,
        state: GameState,
        t: int,
        exclude_mov: Optional[Movement] = None,
    ) -> BoardInterface:
        """Return a proxy board showing all piece positions at time *t*."""
        return self._build_proxy(board, state, t, exclude_mov)

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

    def _build_proxy(
        self,
        board: BoardInterface,
        state: GameState,
        t: int,
        exclude_mov: Optional[Movement] = None,
    ) -> BoardInterface:
        return ProxyBoard(board, self._active_movements, t, self.get_position_at, exclude_mov)

    def resolve_movements(self, board: BoardInterface, state: GameState, current_ms: int) -> None:
        """Advance the simulation to *current_ms*, resolving all arrivals and collisions."""
        active = list(self._active_movements)
        active_cooldowns = list(state.active_cooldowns)
        if not active and not active_cooldowns:
            return

        state.en_passant_targets = [ep for ep in state.en_passant_targets if ep.expires_ms > current_ms]

        t_prev: Optional[int] = None
        for t in self._collect_event_times(active, active_cooldowns, current_ms):
            self._resolve_at(board, state, t, t_prev)
            t_prev = t

        self._expire_cooldowns_le(state, current_ms)

    def _resolve_at(self, board: BoardInterface, state: GameState, t: int, t_prev: Optional[int]) -> None:
        """Resolve collisions, arrivals, and blocked movements at the single instant *t*.

        *t_prev* is the previously resolved instant, which collision detection
        needs to spot pieces that swapped squares between the two.
        """
        self._expire_cooldowns_at(state, t)

        current_active = [mov for mov in self._active_movements if mov.start_ms <= t]
        if not current_active:
            return

        positions = self._positions_at(current_active, t)
        reset_halfmove = self._collision_resolver.resolve(
            board, state, self._active_movements, current_active, positions, t, t_prev
        )
        arrivals_reset, arrivals_increment = self._arrival_resolver.resolve_arrivals(
            board, state, self._active_movements, t, self
        )
        self._arrival_resolver.cancel_blocked_ongoing_movements(
            board, state, self._active_movements, t, self
        )
        self._update_halfmove_clock(
            state, reset_halfmove or arrivals_reset, arrivals_increment
        )

    def _update_halfmove_clock(self, state: GameState, reset: bool, increment: bool) -> None:
        """Apply this instant's outcome to the fifty-move-rule counter."""
        if reset:
            state.halfmove_clock = 0
        elif increment:
            state.halfmove_clock += 1

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

    def _expire_cooldowns_le(self, state: GameState, current_ms: int) -> None:
        expiring_cooldowns = [c for c in state.active_cooldowns if c.end_ms <= current_ms]
        for c in expiring_cooldowns:
            c.piece.transition_to_idle()
            state.active_cooldowns.remove(c)
