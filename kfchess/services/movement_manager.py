from typing import Optional
from kfchess.models.interfaces import BoardInterface, PieceInterface
from kfchess.models.board import Position
from kfchess.models.game_state import GameState, Movement, Cooldown, EnPassantTarget
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.interfaces import (
    MovementDurationInterface,
    MovementManagerInterface,
)
from kfchess.rules.interfaces import (
    PathCheckerInterface,
)
from kfchess.config.game_config import GameConfig
from kfchess.rules.promotion_rules import PromotionStrategyInterface


class InstantMovementDuration(MovementDurationInterface):
    """Strategy that makes all movements instant (0 duration)."""

    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        return 0


class ChebyshevDistanceDuration(MovementDurationInterface):
    """Strategy that calculates duration based on Chebyshev distance."""

    def __init__(self, ms_per_square: int = 1000) -> None:
        self._ms_per_square = ms_per_square

    def calculate_duration(self, frm: Position, to: Position, piece: PieceInterface) -> int:
        dist = max(abs(to.row - frm.row), abs(to.col - frm.col))
        return dist * self._ms_per_square


class ProxyBoard(BoardInterface):
    """A lightweight board representation that dynamically calculates piece positions.
    Avoids copying the entire board array at every simulation step.
    """

    def __init__(
        self,
        board: BoardInterface,
        active_movements: list,
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
        self._moving_piece_ids = set()
        for mov in active_movements:
            if mov == exclude_mov:
                continue
            pos_at_t = get_position_fn(mov, t)
            self._moving_at_pos[pos_at_t] = mov.piece
            self._moving_piece_ids.add(id(mov.piece))

        self._overrides = {}

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


class MovementManager(MovementManagerInterface):
    """Manages active movements, calculates arrival times, and resolves arrivals."""

    def __init__(
        self,
        duration_strategy: MovementDurationInterface,
        move_event_publisher: MoveEventPublisher,
        path_checker: PathCheckerInterface,
        config: GameConfig,
        promotion_strategy: Optional[PromotionStrategyInterface] = None,
    ) -> None:
        self._duration_strategy = duration_strategy
        self._move_event_publisher = move_event_publisher
        self._path_checker = path_checker
        self._config = config
        self._promotion_strategy = promotion_strategy

    def calculate_arrival(self, frm: Position, to: Position, piece: PieceInterface, start_ms: int) -> int:
        duration = self._duration_strategy.calculate_duration(frm, to, piece)
        return start_ms + duration

    def get_position_at(self, mov: Movement, t: int) -> Position:
        if t <= mov.start_ms:
            return mov.frm
        if t >= mov.arrival_ms:
            return mov.to
        if mov.piece.piece_type in self._config.jumper_pieces:
            return mov.frm

        dist = max(abs(mov.to.row - mov.frm.row), abs(mov.to.col - mov.frm.col))
        if dist == 0:
            return mov.frm
        ms_per_square = max(1, (mov.arrival_ms - mov.start_ms) // dist)  # guard: never divide by zero
        step = (t - mov.start_ms) // ms_per_square
        if step >= dist:
            return mov.to

        r_step = (mov.to.row - mov.frm.row) // dist
        c_step = (mov.to.col - mov.frm.col) // dist
        return Position(mov.frm.row + step * r_step, mov.frm.col + step * c_step)

    def get_effective_board(self, board: BoardInterface, state: GameState, t: int) -> BoardInterface:
        return self._get_effective_board(board, state, t)

    def _get_effective_board(self, board: BoardInterface, state: GameState, t: int, exclude_mov: Optional[Movement] = None) -> BoardInterface:
        return ProxyBoard(board, state.active_movements, t, self.get_position_at, exclude_mov)

    def resolve_movements(self, board: BoardInterface, state: GameState, current_ms: int) -> None:
        active = list(state.active_movements)
        active_cooldowns = list(state.active_cooldowns)
        if not active and not active_cooldowns:
            return

        # Clean up expired en_passant_targets based on current_ms
        state.en_passant_targets = [ep for ep in state.en_passant_targets if ep.expires_ms > current_ms]

        # Collect all event times t <= current_ms
        event_times = set()
        for mov in active:
            dist = max(abs(mov.to.row - mov.frm.row), abs(mov.to.col - mov.frm.col))
            event_times.add(mov.start_ms)
            event_times.add(mov.arrival_ms)
            if dist > 1 and mov.piece.piece_type not in self._config.jumper_pieces:
                ms_per_square = max(1, (mov.arrival_ms - mov.start_ms) // dist)  # guard: never divide by zero
                for k in range(1, dist):
                    t = mov.start_ms + k * ms_per_square
                    event_times.add(t)

        for cooldown in active_cooldowns:
            event_times.add(cooldown.end_ms)

        sorted_times = sorted([t for t in event_times if t <= current_ms])

        t_prev = None
        for t in sorted_times:
            # 0. Expire cooldowns that end at t
            expiring_cooldowns = [c for c in state.active_cooldowns if c.end_ms == t]
            for c in expiring_cooldowns:
                c.piece.transition_to_idle()
                state.active_cooldowns.remove(c)

            # 1. Identify currently active movements at time t
            current_active = [mov for mov in state.active_movements if mov.start_ms <= t]
            if not current_active:
                continue

            reset_halfmove = False
            increment_halfmove = False

            # 2. Get positions of all active movements at t
            positions = {id(mov): self.get_position_at(mov, t) for mov in current_active}

            # 3. Check for collisions
            aborted_or_captured = set()
            n = len(current_active)
            for i in range(n):
                for j in range(i + 1, n):
                    mov1 = current_active[i]
                    mov2 = current_active[j]
                    if id(mov1) in aborted_or_captured or id(mov2) in aborted_or_captured:
                        continue

                    pos1 = positions[id(mov1)]
                    pos2 = positions[id(mov2)]

                    # Same square collision
                    same_square = (pos1 == pos2)
                    is_same_sq = (pos1 == pos2)

                    # Crossing collision
                    is_crossing = False
                    if t_prev is not None:
                        pos1_prev = self.get_position_at(mov1, t_prev)
                        pos2_prev = self.get_position_at(mov2, t_prev)
                        if pos1 == pos2_prev and pos2 == pos1_prev and pos1 != pos2:
                            is_crossing = True

                    if not is_same_sq and not is_crossing:
                        continue

                    # Castling exception: Friendly King and Rook arriving at the exact same time do not collide.
                    if mov1.piece.color == mov2.piece.color and mov1.arrival_ms == mov2.arrival_ms:
                        if (mov1.piece.piece_type == "K" and mov2.piece.piece_type == "R") or \
                           (mov1.piece.piece_type == "R" and mov2.piece.piece_type == "K"):
                            continue

                    # Determine winner and loser
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
                            if mov1.start_ms < mov2.start_ms:
                                winner, loser = mov1, mov2
                            elif mov2.start_ms < mov1.start_ms:
                                winner, loser = mov2, mov1
                            else:
                                idx1 = state.active_movements.index(mov1)
                                idx2 = state.active_movements.index(mov2)
                                if idx1 < idx2:
                                    winner, loser = mov1, mov2
                                else:
                                    winner, loser = mov2, mov1

                        aborted_or_captured.add(id(loser))

                        if winner.piece.color != loser.piece.color:
                            # Enemy Collision: loser is captured
                            board.set_piece(loser.frm, None)
                            loser.piece.transition_to_idle()
                            if state.selected_pos == loser.frm:
                                state.selected_pos = None
                            if loser.piece.piece_type in self._config.king_pieces:
                                state.game_over = True
                                state.game_over_reason = "king_captured"
                            reset_halfmove = True
                        else:
                            # Friendly Collision: loser's move is aborted
                            loser.piece.transition_to_cooldown()
                            state.active_cooldowns.append(Cooldown(piece=loser.piece, end_ms=t + self._config.cooldown_duration_ms))
                            if state.selected_pos == loser.frm:
                                state.selected_pos = None

            # Apply deletions to state.active_movements
            for mov in list(state.active_movements):
                if id(mov) in aborted_or_captured:
                    state.active_movements.remove(mov)

            # 4. Handle arrivals at time t
            arriving = [mov for mov in state.active_movements if mov.arrival_ms == t]
            for mov in arriving:
                # Determine whether frm still holds our piece (it may have been
                # overwritten by a piece that landed there during our transit).
                # We must NOT silently eliminate the moving piece just because
                # someone else took its origin square — that is the Phantom Deletion bug.
                frm_still_mine = (board.get_piece(mov.frm) == mov.piece)

                if mov.frm == mov.to:
                    # Successful landing of jump!
                    mov.piece.transition_to_cooldown()
                    state.active_cooldowns.append(Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms))
                    if mov.piece.piece_type == "P":
                        reset_halfmove = True
                    elif mov.frm != mov.to:
                        increment_halfmove = True
                else:
                    # Check if there is an active airborne enemy piece at the destination cell
                    airborne_enemy_jump = None
                    for active_mov in state.active_movements:
                        if (active_mov.frm == active_mov.to and 
                            active_mov.frm == mov.to and 
                            active_mov.start_ms <= t <= active_mov.arrival_ms and 
                            active_mov.piece.color != mov.piece.color):
                            airborne_enemy_jump = active_mov
                            break

                    if airborne_enemy_jump is not None:
                        # The airborne piece captures the arriving enemy!
                        # The arriving enemy is removed.
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
                        # Construct effective board at t excluding mov
                        eff_board = self._get_effective_board(board, state, t, exclude_mov=mov)

                        # Verify path and landing
                        path_clear = self._path_checker.is_path_clear(eff_board, mov.frm, mov.to)
                        ep_targets = [ep.pos for ep in state.en_passant_targets]
                        can_land = self._path_checker.can_land(eff_board, mov.piece, mov.frm, mov.to, ep_targets)

                        if path_clear and can_land:
                            # Successful arrival!
                            target_piece = board.get_piece(mov.to)
                            is_capture = False
                            if target_piece is not None:
                                # Check if target_piece is in flight to a future time
                                is_in_flight_future = False
                                for active_mov in state.active_movements:
                                    if active_mov.piece == target_piece and active_mov.arrival_ms > t:
                                        is_in_flight_future = True
                                        break
                                if not is_in_flight_future:
                                    is_capture = (target_piece.color != mov.piece.color)
                                    if state.selected_pos == mov.to:
                                        state.selected_pos = None
                                    if target_piece.piece_type in self._config.king_pieces:
                                        state.game_over = True
                                        state.game_over_reason = "king_captured"
                                    target_piece.transition_to_idle()
                                    for active_mov in list(state.active_movements):
                                        if active_mov.piece == target_piece:
                                            if active_mov in arriving:
                                                arriving.remove(active_mov)
                                            state.active_movements.remove(active_mov)
                                    for cd in list(state.active_cooldowns):
                                        if cd.piece == target_piece:
                                            state.active_cooldowns.remove(cd)

                            # Clear frm only if this piece still occupies it
                            if frm_still_mine:
                                board.set_piece(mov.frm, None)
                            board.set_piece(mov.to, mov.piece)

                            # Handle En Passant Capture
                            is_ep = False
                            if mov.piece.piece_type == "P":
                                for ep in state.en_passant_targets:
                                    if ep.pos == mov.to:
                                        # It's an En Passant capture. Remove the captured pawn.
                                        captured_piece = board.get_piece(ep.capture_pos)
                                        if captured_piece:
                                            captured_piece.transition_to_idle()
                                        board.set_piece(ep.capture_pos, None)
                                        is_ep = True
                                        break

                            # Handle En Passant Target Creation
                            if mov.piece.piece_type == "P":
                                player_config = self._config.get_player(mov.piece.color)
                                if player_config and abs(mov.to.row - mov.frm.row) == 2 and mov.frm.row in player_config.pawn_start_rows:
                                    ep_pos = Position(mov.frm.row + player_config.forward_direction, mov.frm.col)
                                    state.en_passant_targets.append(EnPassantTarget(pos=ep_pos, capture_pos=mov.to, expires_ms=t + self._config.en_passant_duration_ms))

                            # Evaluate dynamic promotion rules
                            if self._promotion_strategy:
                                self._promotion_strategy.evaluate_promotion(mov.piece, mov.to, self._config)

                            mov.piece.transition_to_cooldown()
                            state.active_cooldowns.append(Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms))
                            self._move_event_publisher.publish(mov.piece, mov.frm, mov.to)

                            if mov.piece.piece_type == "P" or is_capture or is_ep:
                                reset_halfmove = True
                            elif mov.frm != mov.to:
                                increment_halfmove = True
                        else:
                            # Aborted: path blocked or landing invalid (friendly piece)
                            # If frm was overwritten, the piece is gone — eliminate it silently.
                            # If frm is still ours, return to cooldown as normal.
                            if frm_still_mine:
                                mov.piece.transition_to_cooldown()
                                state.active_cooldowns.append(Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms))
                            else:
                                # Origin was taken: piece has nowhere to return — it is eliminated.
                                mov.piece.transition_to_idle()

                # Remove from active movements
                if mov in state.active_movements:
                    state.active_movements.remove(mov)

            if reset_halfmove:
                state.halfmove_clock = 0
            elif increment_halfmove:
                state.halfmove_clock += 1

            t_prev = t

        # Expire any cooldowns that should have expired by current_ms
        expiring_cooldowns = [c for c in state.active_cooldowns if c.end_ms <= current_ms]
        for c in expiring_cooldowns:
            c.piece.transition_to_idle()
            state.active_cooldowns.remove(c)

