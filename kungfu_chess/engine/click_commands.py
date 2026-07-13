"""Click command handling (Layer 5).

Owns: the selection state machine driven by clicks — initial selection,
re-selection, castling attempts (delegated to CastlingCommands), and
move-attempt legality checks (delegated to piece_rules/rule_engine) followed
by enqueuing the resulting Movement.
Must not own: piece-specific movement legality itself, castling legality
itself, or rendering.
"""

from typing import Callable, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.model.game_state import GameState, Movement
from kungfu_chess.rules.piece_rules import MoveValidatorFactoryInterface
from kungfu_chess.rules.rule_engine import PathCheckerInterface, ThreatValidator
from kungfu_chess.realtime.arbiter_interfaces import RealTimeArbiterInterface
from kungfu_chess.engine.engine_interfaces import GameStateRepositoryInterface
from kungfu_chess.engine.castling_commands import CastlingCommands
from kungfu_chess.engine.jump_commands import JumpCommandProcessor


class ClickCommandProcessor:
    """Drives the selection state machine for click commands."""

    def __init__(
        self,
        move_validator_factory: MoveValidatorFactoryInterface,
        path_checker: PathCheckerInterface,
        threat_validator: ThreatValidator,
        arbiter: RealTimeArbiterInterface,
        castling_commands: CastlingCommands,
        jump_commands: JumpCommandProcessor,
        state_repo: GameStateRepositoryInterface,
        resolve_pending: Callable[[], None],
    ) -> None:
        self._move_validator_factory = move_validator_factory
        self._path_checker = path_checker
        self._threat_validator = threat_validator
        self._arbiter = arbiter
        self._castling_commands = castling_commands
        self._jump_commands = jump_commands
        self._state_repo = state_repo
        self._resolve_pending = resolve_pending

    def handle_click(self, state: GameState, board: BoardInterface, target: Position) -> None:
        target_piece = board.get_piece(target)

        if state.selected_pos is None:
            self._handle_initial_selection(state, target, target_piece)
        else:
            self._handle_active_selection_click(state, board, target, target_piece)

        self._state_repo.save_state(state)

    def _handle_initial_selection(
        self,
        state: GameState,
        target: Position,
        target_piece: Optional[PieceInterface]
    ) -> None:
        if target_piece is not None and target_piece.can_select():
            state.selected_pos = target

    def _handle_active_selection_click(
        self,
        state: GameState,
        board: BoardInterface,
        target: Position,
        target_piece: Optional[PieceInterface]
    ) -> None:
        selected_piece = board.get_piece(state.selected_pos)

        if selected_piece is None:
            if target_piece is not None and target_piece.can_select():
                state.selected_pos = target
            else:
                state.selected_pos = None
        elif target == state.selected_pos:
            self._jump_commands.execute_active_jump(state, board, target)
        elif target_piece is not None and target_piece.color == selected_piece.color:
            self._handle_friendly_click(state, board, target, selected_piece, target_piece)
        else:
            self._handle_move_attempt(state, board, target, selected_piece)

    def _handle_friendly_click(
        self,
        state: GameState,
        board: BoardInterface,
        target: Position,
        selected_piece: PieceInterface,
        target_piece: PieceInterface
    ) -> None:
        # Castling check
        if self._castling_commands.is_castle_attempt(selected_piece, target_piece, state.selected_pos, target):
            if self._castling_commands.try_castle(state, board, state.selected_pos, target, selected_piece, target_piece):
                return
        if target_piece.can_select():
            state.selected_pos = target

    def _handle_move_attempt(
        self,
        state: GameState,
        board: BoardInterface,
        target: Position,
        selected_piece: PieceInterface
    ) -> None:
        if not selected_piece.can_move():
            return

        validator = self._move_validator_factory.get_validator(selected_piece.piece_type)
        if not validator.is_legal(state.selected_pos, target, selected_piece.color, board.rows):
            return

        origin = state.selected_pos
        eff_board = self._arbiter.get_effective_board(board, state, state.clock_ms)

        if not self._path_checker.is_path_clear(eff_board, origin, target):
            return

        en_passant_targets = self._arbiter.get_valid_en_passant_positions(board, state, selected_piece.color, state.clock_ms)
        if not self._path_checker.can_land(eff_board, selected_piece, origin, target, en_passant_targets):
            return

        if not self._threat_validator.is_move_safe_from_check(eff_board, origin, target, selected_piece):
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
