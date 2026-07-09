from typing import Optional
from kfchess.models.interfaces import BoardInterface, PieceInterface
from kfchess.models.board import Position
from kfchess.models.game_state import GameState, Movement, Cooldown
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
        ms_per_square = (mov.arrival_ms - mov.start_ms) // dist
        step = (t - mov.start_ms) // ms_per_square
        if step >= dist:
            return mov.to

        r_step = (mov.to.row - mov.frm.row) // dist
        c_step = (mov.to.col - mov.frm.col) // dist
        return Position(mov.frm.row + step * r_step, mov.frm.col + step * c_step)

    def get_effective_board(self, board: BoardInterface, state: GameState, t: int) -> BoardInterface:
        return self._get_effective_board(board, state, t)

    def _get_effective_board(self, board: BoardInterface, state: GameState, t: int, exclude_mov: Optional[Movement] = None) -> BoardInterface:
        from kfchess.models.board import ArrayBoard
        eff_board = ArrayBoard(board.rows, board.cols)

        # Collect moving pieces and their calculated positions at t (excluding exclude_mov)
        moving_pieces = {}
        for mov in state.active_movements:
            if mov == exclude_mov:
                continue
            pos = self.get_position_at(mov, t)
            moving_pieces[id(mov.piece)] = (pos, mov.piece)

        # Populate with static pieces
        for r in range(board.rows):
            for c in range(board.cols):
                pos = Position(r, c)
                piece = board.get_piece(pos)
                if piece is not None:
                    if id(piece) in moving_pieces:
                        # Placing it at its current position
                        eff_pos, _ = moving_pieces[id(piece)]
                        eff_board.set_piece(eff_pos, piece)
                    elif exclude_mov and piece == exclude_mov.piece:
                        # Excluded piece is the one whose move we are checking
                        pass
                    else:
                        if eff_board.get_piece(pos) is None:
                            eff_board.set_piece(pos, piece)

        # Place moving pieces (overwriting static pieces if they occupy the same space)
        for eff_id, (pos, piece) in moving_pieces.items():
            eff_board.set_piece(pos, piece)

        return eff_board

    def resolve_movements(self, board: BoardInterface, state: GameState, current_ms: int) -> None:
        active = list(state.active_movements)
        active_cooldowns = list(state.active_cooldowns)
        if not active and not active_cooldowns:
            return

        # Collect all event times t <= current_ms
        event_times = set()
        for mov in active:
            dist = max(abs(mov.to.row - mov.frm.row), abs(mov.to.col - mov.frm.col))
            event_times.add(mov.start_ms)
            event_times.add(mov.arrival_ms)
            if dist > 1 and mov.piece.piece_type not in self._config.jumper_pieces:
                ms_per_square = (mov.arrival_ms - mov.start_ms) // dist
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

                    # Crossing collision
                    crossing = False
                    if t_prev is not None:
                        pos1_prev = self.get_position_at(mov1, t_prev)
                        pos2_prev = self.get_position_at(mov2, t_prev)
                        if pos1_prev == pos2 and pos1 == pos2_prev:
                            crossing = True

                    if same_square or crossing:
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
                current_piece = board.get_piece(mov.frm)
                if current_piece == mov.piece:
                    if mov.frm == mov.to:
                        # Successful landing of jump!
                        mov.piece.transition_to_cooldown()
                        state.active_cooldowns.append(Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms))
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
                            board.set_piece(mov.frm, None)
                            mov.piece.transition_to_idle()
                            if state.selected_pos == mov.frm:
                                state.selected_pos = None
                            if mov.piece.piece_type in self._config.king_pieces:
                                state.game_over = True
                        else:
                            # Construct effective board at t excluding mov
                            eff_board = self._get_effective_board(board, state, t, exclude_mov=mov)

                            # Verify path and landing
                            path_clear = self._path_checker.is_path_clear(eff_board, mov.frm, mov.to)
                            can_land = self._path_checker.can_land(eff_board, mov.piece, mov.frm, mov.to)

                            if path_clear and can_land:
                                # Successful arrival!
                                target_piece = board.get_piece(mov.to)
                                if target_piece is not None:
                                    if state.selected_pos == mov.to:
                                        state.selected_pos = None
                                    if target_piece.piece_type in self._config.king_pieces:
                                        state.game_over = True

                                board.set_piece(mov.frm, None)
                                board.set_piece(mov.to, mov.piece)

                                # Evaluate dynamic promotion rules
                                if self._promotion_strategy:
                                    self._promotion_strategy.evaluate_promotion(mov.piece, mov.to, self._config)

                                mov.piece.transition_to_cooldown()
                                state.active_cooldowns.append(Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms))
                                self._move_event_publisher.publish(mov.piece, mov.frm, mov.to)
                            else:
                                # Aborted: path blocked or landing invalid (friendly piece)
                                mov.piece.transition_to_cooldown()
                                state.active_cooldowns.append(Cooldown(piece=mov.piece, end_ms=t + self._config.cooldown_duration_ms))
                else:
                    mov.piece.transition_to_idle()

                # Remove from active movements
                if mov in state.active_movements:
                    state.active_movements.remove(mov)

            t_prev = t

        # Expire any cooldowns that should have expired by current_ms
        expiring_cooldowns = [c for c in state.active_cooldowns if c.end_ms <= current_ms]
        for c in expiring_cooldowns:
            c.piece.transition_to_idle()
            state.active_cooldowns.remove(c)

