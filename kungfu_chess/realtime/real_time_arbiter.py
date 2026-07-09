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
from typing import List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.model.game_state import GameState, Movement, Cooldown, EnPassantTarget
from kungfu_chess.rules.rule_engine import PathCheckerInterface
from kungfu_chess.rules.piece_rules import PromotionStrategyInterface


# ---------------------------------------------------------------------------
# Duration strategies (Strategy pattern)
# ---------------------------------------------------------------------------

class MovementDurationInterface(ABC):
    """Calculates the travel duration for a piece moving between two positions."""

    @abstractmethod
    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        """Return the travel duration in milliseconds."""


class InstantMovementDuration(MovementDurationInterface):
    """All movements are instant (0 ms duration)."""

    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        return 0


class ChebyshevDistanceDuration(MovementDurationInterface):
    """Duration is proportional to the Chebyshev distance between squares."""

    def __init__(self, ms_per_square: int = 1000) -> None:
        self._ms_per_square = ms_per_square

    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        dist = max(abs(to.row - frm.row), abs(to.col - frm.col))
        return dist * self._ms_per_square


# ---------------------------------------------------------------------------
# ProxyBoard — efficient effective-board snapshot
# ---------------------------------------------------------------------------

class ProxyBoard(BoardInterface):
    """A read-mostly board view that dynamically overlays active motions.

    Rather than copying the full board at every simulation step, ProxyBoard
    computes piece positions on the fly, making it O(active_motions) per lookup.
    """

    def __init__(
        self,
        board: BoardInterface,
        active_movements: List[Movement],
        t: int,
        get_position_fn,
        exclude_mov: Optional[Movement] = None,
    ) -> None:
        self._board = board
        self._rows = board.rows
        self._cols = board.cols
        self._exclude_mov = exclude_mov
        self._exclude_piece = exclude_mov.piece if exclude_mov is not None else None

        self._moving_at_pos = {}
        self._moving_piece_ids: set = set()
        for mov in active_movements:
            if mov == exclude_mov:
                continue
            pos_at_t = get_position_fn(mov, t)
            self._moving_at_pos[pos_at_t] = mov.piece
            self._moving_piece_ids.add(id(mov.piece))

        self._overrides: dict = {}

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def cols(self) -> int:
        return self._cols

    def is_valid_position(self, pos: Position) -> bool:
        return 0 <= pos.row < self._rows and 0 <= pos.col < self._cols

    def get_piece(self, pos: Position) -> Optional[PieceInterface]:
        if not self.is_valid_position(pos):
            raise IndexError("Position out of board bounds.")
        if pos in self._overrides:
            return self._overrides[pos]
        if pos in self._moving_at_pos:
            return self._moving_at_pos[pos]
        piece = self._board.get_piece(pos)
        if piece is not None:
            if self._exclude_piece and piece == self._exclude_piece:
                return None
            if id(piece) in self._moving_piece_ids:
                return None
            return piece
        return None

    def set_piece(self, pos: Position, piece: Optional[PieceInterface]) -> None:
        if not self.is_valid_position(pos):
            raise IndexError("Position out of board bounds.")
        self._overrides[pos] = piece


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

        # Collect all discrete event times up to current_ms.
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

        sorted_times = sorted(t for t in event_times if t <= current_ms)

        t_prev: Optional[int] = None
        for t in sorted_times:
            # 0. Expire cooldowns ending at t.
            expiring = [c for c in state.active_cooldowns if c.end_ms == t]
            for c in expiring:
                c.piece.transition_to_idle()
                state.active_cooldowns.remove(c)

            # 1. Active movements that have started by t.
            current_active = [mov for mov in state.active_movements if mov.start_ms <= t]
            if not current_active:
                t_prev = t
                continue

            reset_halfmove = False
            increment_halfmove = False

            # 2. Positions of all active movements at t.
            positions = {id(mov): self.get_position_at(mov, t) for mov in current_active}

            # 3. Collision detection.
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
                        if mov1.start_ms < mov2.start_ms:
                            winner, loser = mov1, mov2
                        elif mov2.start_ms < mov1.start_ms:
                            winner, loser = mov2, mov1
                        else:
                            idx1 = state.active_movements.index(mov1)
                            idx2 = state.active_movements.index(mov2)
                            winner, loser = (mov1, mov2) if idx1 < idx2 else (mov2, mov1)

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

            # 4. Handle arrivals at t.
            arriving = [mov for mov in state.active_movements if mov.arrival_ms == t]
            for mov in arriving:
                frm_still_mine = (board.get_piece(mov.frm) == mov.piece)

                if mov.frm == mov.to:
                    # Successful jump-in-place landing.
                    mov.piece.transition_to_cooldown()
                    state.active_cooldowns.append(
                        Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms)
                    )
                    if mov.piece.piece_type == "P":
                        reset_halfmove = True
                else:
                    # Check for an airborne enemy occupying the destination.
                    airborne_enemy_jump: Optional[Movement] = None
                    for active_mov in state.active_movements:
                        if (active_mov.frm == active_mov.to
                                and active_mov.frm == mov.to
                                and active_mov.start_ms <= t <= active_mov.arrival_ms
                                and active_mov.piece.color != mov.piece.color):
                            airborne_enemy_jump = active_mov
                            break

                    if airborne_enemy_jump is not None:
                        # Airborne piece captures the arriving enemy.
                        if frm_still_mine:
                            board.set_piece(mov.frm, None)
                        mov.piece.transition_to_idle()
                        if state.selected_pos == mov.frm:
                            state.selected_pos = None
                        if mov.piece.piece_type in self._config.king_pieces:
                            state.game_over = True
                            state.game_over_reason = "king_captured"
                        reset_halfmove = True
                    else:
                        eff_board = self._build_proxy(board, state, t, exclude_mov=mov)
                        path_clear = self._path_checker.is_path_clear(eff_board, mov.frm, mov.to)
                        ep_targets = [ep.pos for ep in state.en_passant_targets]
                        can_land = self._path_checker.can_land(eff_board, mov.piece, mov.frm, mov.to, ep_targets)

                        if path_clear and can_land:
                            # Successful arrival!
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

                            if frm_still_mine:
                                board.set_piece(mov.frm, None)
                            board.set_piece(mov.to, mov.piece)

                            # En-passant capture.
                            is_ep = False
                            if mov.piece.piece_type == "P":
                                for ep in state.en_passant_targets:
                                    if ep.pos == mov.to:
                                        captured_piece = board.get_piece(ep.capture_pos)
                                        if captured_piece:
                                            captured_piece.transition_to_idle()
                                        board.set_piece(ep.capture_pos, None)
                                        is_ep = True
                                        break

                            # En-passant target creation.
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

                            # Promotion.
                            if self._promotion_strategy:
                                self._promotion_strategy.evaluate_promotion(mov.piece, mov.to, self._config)

                            mov.piece.transition_to_cooldown()
                            state.active_cooldowns.append(
                                Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms)
                            )
                            if self._move_event_publisher:
                                self._move_event_publisher.publish(mov.piece, mov.frm, mov.to)

                            if mov.piece.piece_type == "P" or is_capture or is_ep:
                                reset_halfmove = True
                            elif mov.frm != mov.to:
                                increment_halfmove = True
                        else:
                            # Aborted: path blocked or landing invalid.
                            if frm_still_mine:
                                mov.piece.transition_to_cooldown()
                                state.active_cooldowns.append(
                                    Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms)
                                )
                            else:
                                # Origin taken — piece is eliminated.
                                mov.piece.transition_to_idle()

                if mov in state.active_movements:
                    state.active_movements.remove(mov)

            # 5. Early cancellation of ongoing movements
            ongoing = [mov for mov in state.active_movements if mov.start_ms <= t and mov.arrival_ms > t]
            for mov in ongoing:
                frm_still_mine = (board.get_piece(mov.frm) == mov.piece)
                eff_board = self._build_proxy(board, state, t, exclude_mov=mov)
                path_clear = self._path_checker.is_path_clear(eff_board, mov.frm, mov.to)
                ep_targets = [ep.pos for ep in state.en_passant_targets]
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

            if reset_halfmove:
                state.halfmove_clock = 0
            elif increment_halfmove:
                state.halfmove_clock += 1

            t_prev = t

        # Expire any cooldowns that should have ended by current_ms.
        expiring_cooldowns = [c for c in state.active_cooldowns if c.end_ms <= current_ms]
        for c in expiring_cooldowns:
            c.piece.transition_to_idle()
            state.active_cooldowns.remove(c)
