from typing import Optional
from kfchess.models.board import Position
from kfchess.repositories.interfaces import BoardrepositoriesInterface, GameStaterepositoriesInterface
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.game_play_state import GamePlayStateFactory
from kfchess.services.interfaces import (
    BoardPrinterInterface,
    CommandExecutorInterface,
    MoveValidatorFactoryInterface,
    PathCheckerInterface,
    MovementManagerInterface,
)

# Each board cell is 100×100 pixels.
_CELL_SIZE_PX: int = 100


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
        movement_manager: Optional[MovementManagerInterface] = None,
        game_play_state_factory: Optional[GamePlayStateFactory] = None,
    ) -> None:
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._printer = printer
        self._move_validator_factory = move_validator_factory
        self._move_event_publisher = move_event_publisher
        self._path_checker = path_checker

        if movement_manager is None:
            from kfchess.services.movement_manager import MovementManager, InstantMovementDuration
            movement_manager = MovementManager(
                duration_strategy=InstantMovementDuration(),
                move_event_publisher=move_event_publisher,
                path_checker=path_checker,
            )
        self._movement_manager = movement_manager

        if game_play_state_factory is None:
            game_play_state_factory = GamePlayStateFactory()
        self._game_play_state_factory = game_play_state_factory

    # ------------------------------------------------------------------
    # CommandExecutorInterface
    # ------------------------------------------------------------------

    def execute_command(self, command: str) -> None:
        parts = command.split()
        if not parts:
            return

        if parts[0] == "click" and len(parts) == 3:
            self._handle_click(int(parts[1]), int(parts[2]))
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
        self._movement_manager.resolve_movements(board, state, state.clock_ms)
        self._board_repo.save_board(board)
        self._state_repo.save_state(state)

    def _handle_click(self, x: int, y: int) -> None:
        self._resolve_pending()
        state = self._state_repo.get_state()
        play_state = self._game_play_state_factory.get_state(state.game_over)
        play_state.handle_click(self, x, y)

    def _execute_active_click(self, x: int, y: int) -> None:
        board = self._board_repo.get_board()
        if board is None:
            return

        col = x // _CELL_SIZE_PX
        row = y // _CELL_SIZE_PX
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
            elif (
                target_piece is not None
                and target_piece.color == selected_piece.color
            ):
                # Friendly piece — replace the selection if it is not moving.
                if target_piece.can_select():
                    state.selected_pos = target
            else:
                # ── Attempt to move ──────────────────────────────────
                if not selected_piece.can_move():
                    return

                from kfchess.models.piece import Color
                opp_color = Color.BLACK if selected_piece.color == Color.WHITE else Color.WHITE
                is_capture = target_piece is not None and target_piece.color == opp_color
                if not is_capture:
                    if any(mov.piece.color == opp_color for mov in state.active_movements):
                        return

                validator = self._move_validator_factory.get_validator(
                    selected_piece.piece_type
                )
                if not validator.is_legal(state.selected_pos, target, selected_piece.color):
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
                if not self._path_checker.can_land(eff_board, selected_piece, origin, target):
                    # Friendly piece on target — keep selection.
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

