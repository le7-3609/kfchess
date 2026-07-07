from kfchess.models.board import Position
from kfchess.repositories.interfaces import BoardrepositoriesInterface, GameStaterepositoriesInterface
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.interfaces import (
    BoardPrinterInterface,
    CommandExecutorInterface,
    MoveValidatorFactoryInterface,
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
    ) -> None:
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._printer = printer
        self._move_validator_factory = move_validator_factory
        self._move_event_publisher = move_event_publisher

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

    def _handle_click(self, x: int, y: int) -> None:
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
                state.selected_pos = target  # Select this piece.
            # else: empty cell with no selection → ignored.
        else:
            # ── A piece is already selected ──────────────────────────
            selected_piece = board.get_piece(state.selected_pos)

            if selected_piece is None:
                # Stale selection: selected cell is empty (already moved).
                # Start fresh — select the newly clicked piece if any.
                state.selected_pos = target if target_piece is not None else None
            elif (
                target_piece is not None
                and target_piece.color == selected_piece.color
            ):
                # Friendly piece — replace the selection.
                state.selected_pos = target
            else:
                # ── Attempt to move ──────────────────────────────────
                validator = self._move_validator_factory.get_validator(
                    selected_piece.piece_type
                )
                if not validator.is_legal(state.selected_pos, target):
                    # Illegal move shape — keep selection, do nothing.
                    return

                # Legal move: commit and fire an event.
                origin = state.selected_pos
                board.set_piece(target, selected_piece)
                board.set_piece(origin, None)
                state.selected_pos = None
                self._board_repo.save_board(board)
                self._move_event_publisher.publish(selected_piece, origin, target)

        self._state_repo.save_state(state)

    def _handle_wait(self, ms: int) -> None:
        state = self._state_repo.get_state()
        state.clock_ms += ms
        self._state_repo.save_state(state)

    def _handle_print_board(self) -> None:
        board = self._board_repo.get_board()
        if board is not None:
            self._printer.print_board(board)
