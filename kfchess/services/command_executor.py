from typing import Optional
from kfchess.models.board import Position
from kfchess.repositories.interfaces import BoardrepositoriesInterface, GameStaterepositoriesInterface
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.game_play_state import GamePlayStateFactory
from kfchess.services.interfaces import (
    BoardPrinterInterface,
    CommandExecutorInterface,
    MovementManagerInterface,
)
from kfchess.rules.interfaces import (
    MoveValidatorFactoryInterface,
    PathCheckerInterface,
)
from kfchess.services.threat_validator import ThreatValidator
from kfchess.services.endgame_validator import EndgameValidator

from kfchess.config.game_config import GameConfig


class CommandExecutor(CommandExecutorInterface):

    """
    Executes the three supported text commands against the board and game state.

    Pixel-to-cell mapping
    ---------------------
    col = x // CELL_SIZE_PX
    row = y // CELL_SIZE_PX

    Click semantics
    ---------------
    * No selection active:
        - Cell has a piece  → select it (any colour).
        - Cell is empty     → ignored.
    * Selection is active (the selected piece has colour C):
        - Cell has a piece of colour C → replace selection.
        - Otherwise (empty or opponent's piece):
            - Move is geometrically legal   → execute move, clear selection,
                                              publish MoveEvent.
            - Move is geometrically illegal → keep selection, ignore click.

    Wait semantics
    --------------
    Advances GameState.clock_ms by the given number of milliseconds.

    Print board
    -----------
    Delegates to the injected BoardPrinterInterface.
    """

    def __init__(
        self,
        board_repo: BoardrepositoriesInterface,
        state_repo: GameStaterepositoriesInterface,
        printer: BoardPrinterInterface,
        move_validator_factory: MoveValidatorFactoryInterface,
        move_event_publisher: MoveEventPublisher,
        path_checker: PathCheckerInterface,
        config: GameConfig,
        movement_manager: Optional[MovementManagerInterface] = None,
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

        if movement_manager is None:
            from kfchess.services.movement_manager import MovementManager, InstantMovementDuration
            from kfchess.rules.promotion_rules import StandardPawnPromotion
            movement_manager = MovementManager(
                duration_strategy=InstantMovementDuration(),
                move_event_publisher=move_event_publisher,
                path_checker=path_checker,
                config=config,
                promotion_strategy=StandardPawnPromotion(),
            )
        self._movement_manager = movement_manager

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
                movement_manager=self._movement_manager,
                threat_validator=self._threat_validator,
                config=config,
            )
        self._endgame_validator = endgame_validator

    # ------------------------------------------------------------------
    # CommandExecutorInterface
    # ------------------------------------------------------------------

    def execute_command(self, command: str) -> None:
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
    # Private command handlers
    # ------------------------------------------------------------------

    def _resolve_pending(self) -> None:
        board = self._board_repo.get_board()
        if board is None:
            return
        state = self._state_repo.get_state()
        
        from kfchess.services.endgame_validator import serialize_board_state
        if not state.position_history:
            state.position_history.append(serialize_board_state(board, state))
            
        self._movement_manager.resolve_movements(board, state, state.clock_ms)
        
        current_serialized = serialize_board_state(board, state)
        if state.position_history[-1] != current_serialized:
            state.position_history.append(current_serialized)
            
        self._check_game_end_conditions(board, state)
        self._board_repo.save_board(board)
        self._state_repo.save_state(state)

    def _check_game_end_conditions(self, board, state) -> None:
        if state.game_over:
            return
            
        # Require both kings to be present to check checkmate/stalemate/draws
        if not self._endgame_validator._has_king(board, "w") or not self._endgame_validator._has_king(board, "b"):
            return
            
        for color in ["w", "b"]:
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
        state = self._state_repo.get_state()
        play_state = self._game_play_state_factory.get_state(state.game_over)
        play_state.handle_click(self, x, y)

    def _execute_active_click(self, x: int, y: int) -> None:
        board = self._board_repo.get_board()
        if board is None:
            return

        col = x // self._config.cell_size_px
        row = y // self._config.cell_size_px
        target = Position(row, col)

        if not board.is_valid_position(target):
            return  # Click outside the board — ignored.

        state = self._state_repo.get_state()
        target_piece = board.get_piece(target)

        if state.selected_pos is None:
            # ── No active selection ──────────────────────────────────
            if target_piece is not None:
                # If target is currently moving, do not select it.
                if not target_piece.can_select():
                    return
                state.selected_pos = target  # Select this piece.
            # else: empty cell with no selection → ignored.
        else:
            # ── A piece is already selected ──────────────────────────
            selected_piece = board.get_piece(state.selected_pos)

            if selected_piece is None:
                # Stale selection: selected cell is empty (already moved).
                # Start fresh — select the newly clicked piece if any.
                if target_piece is not None and target_piece.can_select():
                    state.selected_pos = target
                else:
                    state.selected_pos = None
            elif target == state.selected_pos:
                # Clicking the currently selected piece again triggers a jump.
                self._execute_active_jump(x, y)
            elif (
                target_piece is not None
                and target_piece.color == selected_piece.color
            ):
                # Castling Check
                if (selected_piece.piece_type in self._config.king_pieces and 
                    target_piece.piece_type == "R" and 
                    not selected_piece.has_moved and 
                    not target_piece.has_moved):
                    if self._try_castle(state, board, state.selected_pos, target, selected_piece, target_piece):
                        return
                        
                # Friendly piece — replace the selection if it is not moving.
                if target_piece.can_select():
                    state.selected_pos = target
            else:
                # ── Attempt to move ──────────────────────────────────
                if not selected_piece.can_move():
                    return

                # Identify opponents based on player configuration
                # Assuming color is the player's ID, anything else is opponent
                is_en_passant = selected_piece.piece_type == "P" and any(target == ep.pos for ep in state.en_passant_targets)
                is_capture = (target_piece is not None and target_piece.color != selected_piece.color) or is_en_passant
                
                if not is_capture:
                    if any(mov.piece.color != selected_piece.color for mov in state.active_movements):
                        return

                validator = self._move_validator_factory.get_validator(
                    selected_piece.piece_type
                )
                if not validator.is_legal(state.selected_pos, target, selected_piece.color, board_rows=board.rows):
                    # Illegal move shape — keep selection, do nothing.
                    return

                origin = state.selected_pos

                # Use effective board at current clock time to perform checks
                eff_board = self._movement_manager.get_effective_board(board, state, state.clock_ms)

                # Check that the path between origin and target is clear.
                if not self._path_checker.is_path_clear(eff_board, origin, target):
                    # Blocked by an intervening piece — keep selection.
                    return

                # Check that landing is allowed (no friendly piece on target).
                en_passant_targets = [ep.pos for ep in state.en_passant_targets]
                if not self._path_checker.can_land(eff_board, selected_piece, origin, target, en_passant_targets):
                    # Friendly piece on target — keep selection.
                    return

                # King Threat Validation (Check & Pin Prevention)
                # We simulate the move on the effective board to ensure the player's king is not left in check
                original_target_piece = eff_board.get_piece(target)
                eff_board.set_piece(origin, None)
                eff_board.set_piece(target, selected_piece)
                
                is_threatened = self._threat_validator.is_king_threatened(eff_board, selected_piece.color)
                
                # Revert simulation
                eff_board.set_piece(origin, selected_piece)
                eff_board.set_piece(target, original_target_piece)

                if is_threatened:
                    # Move leaves King in check - reject move, keep selection.
                    return

                # Queue the movement. Do NOT modify the board immediately.
                arrival_ms = self._movement_manager.calculate_arrival(
                    origin, target, selected_piece, state.clock_ms
                )
                from kfchess.models.game_state import Movement
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

                # Instantly resolve if duration was 0
                self._resolve_pending()

        self._state_repo.save_state(state)

    def _handle_jump(self, x: int, y: int) -> None:
        self._resolve_pending()
        state = self._state_repo.get_state()
        play_state = self._game_play_state_factory.get_state(state.game_over)
        play_state.handle_jump(self, x, y)

    def _execute_active_jump(self, x: int, y: int) -> None:
        board = self._board_repo.get_board()
        if board is None:
            return

        col = x // self._config.cell_size_px
        row = y // self._config.cell_size_px
        target = Position(row, col)

        if not board.is_valid_position(target):
            return

        state = self._state_repo.get_state()
        piece = board.get_piece(target)

        if piece is not None:
            # Check constraints:
            # "A moving piece cannot jump." -> represented by not piece.can_move()
            # "A captured piece cannot jump." -> if it is on the board, it's not captured.
            if not piece.can_move():
                print(f"[DEBUG JUMP] piece {piece.piece_type} cannot move! state={piece._state}")
                return

            arrival_ms = state.clock_ms + self._config.jump_duration_ms
            from kfchess.models.game_state import Movement
            mov = Movement(
                frm=target,
                to=target,
                piece=piece,
                start_ms=state.clock_ms,
                arrival_ms=arrival_ms,
            )
            print(f"[DEBUG JUMP] Added {piece.piece_type} to active_movements!")
            state.active_movements.append(mov)
            piece.transition_to_jumping()
            # Clear selection if we are jumping the selected piece
            if state.selected_pos == target:
                state.selected_pos = None
            self._state_repo.save_state(state)
        else:
            print(f"[DEBUG JUMP] No piece at {target}?!")

    def _handle_wait(self, ms: int) -> None:
        state = self._state_repo.get_state()
        state.clock_ms += ms
        self._state_repo.save_state(state)
        self._resolve_pending()

    def _handle_print_board(self) -> None:
        self._resolve_pending()
        board = self._board_repo.get_board()
        if board is not None:
            self._printer.print_board(board)

    def _try_castle(self, state, board, king_pos, rook_pos, king_piece, rook_piece) -> bool:
        eff_board = self._movement_manager.get_effective_board(board, state, state.clock_ms)
        
        dc = 1 if rook_pos.col > king_pos.col else -1
        
        # Manually verify path is clear for castling
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
        
        from kfchess.models.game_state import Movement
        king_arrival = self._movement_manager.calculate_arrival(king_pos, king_dest, king_piece, state.clock_ms)
        king_mov = Movement(frm=king_pos, to=king_dest, piece=king_piece, start_ms=state.clock_ms, arrival_ms=king_arrival)
        
        rook_arrival = king_arrival
        rook_mov = Movement(frm=rook_pos, to=rook_dest, piece=rook_piece, start_ms=state.clock_ms, arrival_ms=rook_arrival)
        
        state.active_movements.extend([rook_mov, king_mov])
        king_piece.transition_to_moving()
        rook_piece.transition_to_moving()
        
        state.selected_pos = None
        self._state_repo.save_state(state)
        self._resolve_pending()
        return True

