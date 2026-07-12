"""Game engine — application-service coordination (Layer 5).

Owns: game-over guard, validation delegation, starting legal motions,
      wait delegation, and board snapshots.
Must not own: piece-specific movement logic, rendering, input parsing,
              DSL parsing, or pixel mapping.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.game_state import GameState, Movement
from kungfu_chess.model.game_state import Result
from kungfu_chess.rules.piece_rules import MoveValidatorFactoryInterface
from kungfu_chess.rules.rule_engine import (
    PathCheckerInterface,
    ThreatValidator,
    EndgameValidator,
    serialize_board_state,
)
from kungfu_chess.realtime.real_time_arbiter import RealTimeArbiterInterface


# ---------------------------------------------------------------------------
# Repository interfaces (owned by engine so it can decouple from storage)
# ---------------------------------------------------------------------------

class BoardRepositoryInterface(ABC):
    @abstractmethod
    def get_board(self) -> Optional[BoardInterface]:
        """Retrieve the currently stored board."""

    @abstractmethod
    def save_board(self, board: BoardInterface) -> None:
        """Persist the given board."""


class GameStateRepositoryInterface(ABC):
    @abstractmethod
    def get_state(self) -> GameState:
        """Retrieve the current game state."""

    @abstractmethod
    def save_state(self, state: GameState) -> None:
        """Persist the given game state."""


# ---------------------------------------------------------------------------
# Event publisher interface
# ---------------------------------------------------------------------------

class MoveEventListenerInterface(ABC):
    """Observer notified when a piece is successfully moved."""

    @abstractmethod
    def on_move(self, piece, frm: Position, to: Position) -> None:
        """Called after a legal move has been committed to the board."""


class MoveEventPublisher:
    """Subject in the Observer pattern; notifies registered listeners."""

    def __init__(self) -> None:
        self._listeners: List[MoveEventListenerInterface] = []

    def subscribe(self, listener: MoveEventListenerInterface) -> None:
        self._listeners.append(listener)

    def publish(self, piece, frm: Position, to: Position) -> None:
        for listener in self._listeners:
            listener.on_move(piece, frm, to)


# ---------------------------------------------------------------------------
# GamePlayState (State pattern for active / game-over)
# ---------------------------------------------------------------------------

class GamePlayState(ABC):
    """Represents the current play state (active or game-over)."""

    @abstractmethod
    def handle_click(self, engine: 'GameEngine', target: Position) -> None:
        """Handle a click in this state."""

    @abstractmethod
    def handle_jump(self, engine: 'GameEngine', target: Position) -> None:
        """Handle a jump in this state."""


class ActivePlayState(GamePlayState):
    def handle_click(self, engine: 'GameEngine', target: Position) -> None:
        engine._execute_active_click(target)

    def handle_jump(self, engine: 'GameEngine', target: Position) -> None:
        engine._execute_active_jump(target)


class GameOverPlayState(GamePlayState):
    def handle_click(self, engine: 'GameEngine', target: Position) -> None:
        pass  # Ignored after game over.

    def handle_jump(self, engine: 'GameEngine', target: Position) -> None:
        pass  # Ignored after game over.


class GamePlayStateFactory:
    def get_state(self, game_over: bool) -> GamePlayState:
        return GameOverPlayState() if game_over else ActivePlayState()


# ---------------------------------------------------------------------------
# BoardPrinter interface
# ---------------------------------------------------------------------------

class BoardPrinterInterface(ABC):
    @abstractmethod
    def print_board(self, board: BoardInterface) -> None:
        """Write the board layout to an output stream."""


# ---------------------------------------------------------------------------
# GameEngine
# ---------------------------------------------------------------------------

class GameEngine:
    """Application-service coordinator.

    Handles:
      - Click/jump/wait/print-board command dispatching
      - Delegating move validation to piece_rules and rule_engine
      - Starting legal motions via the arbiter
      - Game-over detection after each command
      - Castling orchestration

    Pixel-to-cell mapping is delegated to a BoardMapper (see input/board_mapper.py).
    Selection state is owned by the Controller (see input/controller.py).
    The GameEngine therefore receives *already-resolved* board positions from
    the Controller when handling clicks.
    """

    def __init__(
        self,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        printer: BoardPrinterInterface,
        move_validator_factory: MoveValidatorFactoryInterface,
        move_event_publisher: MoveEventPublisher,
        path_checker: PathCheckerInterface,
        config: 'GameConfig',  # type: ignore[name-defined]
        arbiter: Optional[RealTimeArbiterInterface] = None,
        game_play_state_factory: Optional[GamePlayStateFactory] = None,
        threat_validator: Optional[ThreatValidator] = None,
        endgame_validator: Optional[EndgameValidator] = None,
    ) -> None:
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._printer = printer
        self._move_validator_factory = move_validator_factory
        self._move_event_publisher = move_event_publisher
        self._path_checker = path_checker
        self._config = config

        from kungfu_chess.input.board_mapper import BoardMapper
        self._board_mapper = BoardMapper(self._config.cell_size_px)

        # Arbiter — default to instant movement if not provided.
        if arbiter is None:
            from kungfu_chess.realtime.real_time_arbiter import (
                RealTimeArbiter, InstantMovementDuration,
            )
            from kungfu_chess.rules.piece_rules import StandardPawnPromotion
            arbiter = RealTimeArbiter(
                duration_strategy=InstantMovementDuration(),
                path_checker=path_checker,
                config=config,
                promotion_strategy=StandardPawnPromotion(),
                move_event_publisher=move_event_publisher,
            )
        self._arbiter = arbiter

        if threat_validator is None:
            threat_validator = ThreatValidator(
                move_validator_factory=move_validator_factory,
                path_checker=path_checker,
                config=config,
            )
        self._threat_validator = threat_validator

        if game_play_state_factory is None:
            game_play_state_factory = GamePlayStateFactory()
        self._game_play_state_factory = game_play_state_factory

        if endgame_validator is None:
            endgame_validator = EndgameValidator(
                move_validator_factory=move_validator_factory,
                path_checker=path_checker,
                movement_manager=self._arbiter,
                threat_validator=self._threat_validator,
                config=config,
            )
        self._endgame_validator = endgame_validator

    # ------------------------------------------------------------------
    # Public command dispatcher
    # ------------------------------------------------------------------

    def execute_command(self, command: str) -> None:
        """Execute a single text command against the current game state."""
        parts = command.split()
        if not parts:
            return

        if parts[0] == "click" and len(parts) == 3:
            self._handle_click(int(parts[1]), int(parts[2]))
        elif parts[0] == "jump" and len(parts) == 3:
            self._handle_jump(int(parts[1]), int(parts[2]))
        elif parts[0] == "wait" and len(parts) == 2:
            self._handle_wait(int(parts[1]))
        elif command == "print board":
            self._handle_print_board()
        # Unknown commands are silently ignored.

    # ------------------------------------------------------------------
    # Internal command handlers
    # ------------------------------------------------------------------

    def _resolve_pending(self) -> None:
        """Resolve all pending motions at the current clock time."""
        board = self._board_repo.get_board()
        if board is None:
            return
        state = self._state_repo.get_state()

        if not state.position_history:
            state.position_history.append(serialize_board_state(board, state))

        self._arbiter.resolve_movements(board, state, state.clock_ms)

        current_serialized = serialize_board_state(board, state)
        if state.position_history[-1] != current_serialized:
            state.position_history.append(current_serialized)

        self._check_game_end_conditions(board, state)
        self._board_repo.save_board(board)
        self._state_repo.save_state(state)

    def _check_game_end_conditions(self, board: BoardInterface, state: GameState) -> None:
        if state.game_over:
            return
        has_w = self._endgame_validator._has_king(board, "w")
        has_b = self._endgame_validator._has_king(board, "b")
        if not has_w or not has_b:
            return

        for color in ("w", "b"):
            if self._endgame_validator.is_checkmate(board, state, color):
                state.game_over = True
                state.game_over_reason = "checkmate"
                return
            if self._endgame_validator.is_stalemate(board, state, color):
                state.game_over = True
                state.game_over_reason = "stalemate"
                return

        if self._endgame_validator.is_insufficient_material(board):
            state.game_over = True
            state.game_over_reason = "insufficient_material"
            return
        if self._endgame_validator.is_threefold_repetition(board, state):
            state.game_over = True
            state.game_over_reason = "threefold_repetition"
            return
        if self._endgame_validator.is_fifty_move_rule(board, state):
            state.game_over = True
            state.game_over_reason = "fifty_move_rule"
            return

    def _handle_click(self, x: int, y: int) -> None:
        self._resolve_pending()
        board = self._board_repo.get_board()
        if board is None:
            return
        target = self._board_mapper.pixel_to_position(x, y, board)
        if target is None:
            return
        state = self._state_repo.get_state()
        play_state = self._game_play_state_factory.get_state(state.game_over)
        play_state.handle_click(self, target)

    def _execute_active_click(self, target: Position) -> None:
        board = self._board_repo.get_board()
        if board is None:
            return

        if not board.is_valid_position(target):
            return

        state = self._state_repo.get_state()
        target_piece = board.get_piece(target)

        if state.selected_pos is None:
            if target_piece is not None:
                if not target_piece.can_select():
                    return
                state.selected_pos = target
        else:
            selected_piece = board.get_piece(state.selected_pos)

            if selected_piece is None:
                if target_piece is not None and target_piece.can_select():
                    state.selected_pos = target
                else:
                    state.selected_pos = None
            elif target == state.selected_pos:
                self._execute_active_jump(target)
            elif target_piece is not None and target_piece.color == selected_piece.color:
                # Castling check
                if (selected_piece.piece_type in self._config.king_pieces
                        and target_piece.piece_type == "R"
                        and not selected_piece.has_moved
                        and not target_piece.has_moved):
                    if self._try_castle(state, board, state.selected_pos, target, selected_piece, target_piece):
                        return
                if target_piece.can_select():
                    state.selected_pos = target
            else:
                if not selected_piece.can_move():
                    return

                is_en_passant = selected_piece.piece_type == "P" and any(
                    target == ep.pos for ep in state.en_passant_targets
                )

                validator = self._move_validator_factory.get_validator(selected_piece.piece_type)
                if not validator.is_legal(state.selected_pos, target, selected_piece.color, board.rows):
                    return

                origin = state.selected_pos
                eff_board = self._arbiter.get_effective_board(board, state, state.clock_ms)

                if not self._path_checker.is_path_clear(eff_board, origin, target):
                    return

                en_passant_targets = [ep.pos for ep in state.en_passant_targets]
                if not self._path_checker.can_land(eff_board, selected_piece, origin, target, en_passant_targets):
                    return

                # Simulate move to check for self-check.
                original_target_piece = eff_board.get_piece(target)
                eff_board.set_piece(origin, None)
                eff_board.set_piece(target, selected_piece)
                is_threatened = self._threat_validator.is_king_threatened(eff_board, selected_piece.color)
                eff_board.set_piece(origin, selected_piece)
                eff_board.set_piece(target, original_target_piece)

                if is_threatened:
                    return

                arrival_ms = self._arbiter.calculate_arrival(origin, target, selected_piece, state.clock_ms)
                mov = Movement(
                    frm=origin,
                    to=target,
                    piece=selected_piece,
                    start_ms=state.clock_ms,
                    arrival_ms=arrival_ms,
                )
                state.active_movements.append(mov)
                selected_piece.transition_to_moving()
                state.selected_pos = None
                self._state_repo.save_state(state)
                self._resolve_pending()

        self._state_repo.save_state(state)

    def _handle_jump(self, x: int, y: int) -> None:
        self._resolve_pending()
        board = self._board_repo.get_board()
        if board is None:
            return
        target = self._board_mapper.pixel_to_position(x, y, board)
        if target is None:
            return
        state = self._state_repo.get_state()
        play_state = self._game_play_state_factory.get_state(state.game_over)
        play_state.handle_jump(self, target)

    def _execute_active_jump(self, target: Position) -> None:
        board = self._board_repo.get_board()
        if board is None:
            return

        if not board.is_valid_position(target):
            return

        state = self._state_repo.get_state()
        piece = board.get_piece(target)

        if piece is not None:
            if not piece.can_move():
                return

            arrival_ms = state.clock_ms + self._config.jump_duration_ms
            mov = Movement(
                frm=target,
                to=target,
                piece=piece,
                start_ms=state.clock_ms,
                arrival_ms=arrival_ms,
            )
            state.active_movements.append(mov)
            piece.transition_to_jumping()
            if state.selected_pos == target:
                state.selected_pos = None
            self._state_repo.save_state(state)

    def _handle_wait(self, ms: int) -> None:
        if ms <= 0:
            return
        state = self._state_repo.get_state()
        state.clock_ms += ms
        self._state_repo.save_state(state)
        self._resolve_pending()

    def _handle_print_board(self) -> None:
        self._resolve_pending()
        board = self._board_repo.get_board()
        if board is not None:
            self._printer.print_board(board)

    def _try_castle(
        self,
        state: GameState,
        board: BoardInterface,
        king_pos: Position,
        rook_pos: Position,
        king_piece,
        rook_piece,
    ) -> bool:
        eff_board = self._arbiter.get_effective_board(board, state, state.clock_ms)

        dc = 1 if rook_pos.col > king_pos.col else -1

        cur_col = king_pos.col + dc
        while cur_col != rook_pos.col:
            if eff_board.get_piece(Position(king_pos.row, cur_col)) is not None:
                return False
            cur_col += dc

        king_dest_col = king_pos.col + 2 * dc
        rook_dest_col = king_pos.col + 1 * dc

        if not (0 <= king_dest_col < board.cols) or not (0 <= rook_dest_col < board.cols):
            return False

        king_dest = Position(king_pos.row, king_dest_col)
        rook_dest = Position(rook_pos.row, rook_dest_col)
        pass_pos = Position(king_pos.row, king_pos.col + dc)

        for pos_to_check in [king_pos, pass_pos, king_dest]:
            eff_board.set_piece(king_pos, None)
            eff_board.set_piece(pos_to_check, king_piece)
            threatened = self._threat_validator.is_king_threatened(eff_board, king_piece.color)
            eff_board.set_piece(pos_to_check, None)
            if threatened:
                eff_board.set_piece(king_pos, king_piece)
                return False

        eff_board.set_piece(king_pos, king_piece)

        king_arrival = self._arbiter.calculate_arrival(king_pos, king_dest, king_piece, state.clock_ms)
        rook_arrival = king_arrival

        king_mov = Movement(frm=king_pos, to=king_dest, piece=king_piece, start_ms=state.clock_ms, arrival_ms=king_arrival)
        rook_mov = Movement(frm=rook_pos, to=rook_dest, piece=rook_piece, start_ms=state.clock_ms, arrival_ms=rook_arrival)

        state.active_movements.extend([rook_mov, king_mov])
        king_piece.transition_to_moving()
        rook_piece.transition_to_moving()
        state.selected_pos = None
        self._state_repo.save_state(state)
        self._resolve_pending()
        return True
