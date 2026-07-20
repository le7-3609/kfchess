"""GameService — the single application-facade the UI and text tests both drive.

This is the one boundary the outside world talks to. It owns parse/validate/
build/execute wiring, and exposes two kinds of operations:

  - Commands (mutate): init_game(), execute_command(), execute_commands(),
    click(), right_click(), advance_clock(), plus history save/load.
  - Queries (read-only): get_snapshot(), get_moves(), list_saves(),
    load_saved_game(), game_over state.

Callers (the tk UI, the script runner, bots) never reach past this facade to
the GameEngine, the board/state repositories, or the arbiter directly.

It is also the subscription point for the domain EventBus: observers register
through subscribe() here rather than being handed the engine's bus, so "who
may listen" stays a property of the facade like everything else.
"""

from typing import List, Optional, Type

from shared.config import consts
from shared.config.game_config import GameConfig
from shared.events import Event, EventBus, GameStartedEvent, Observer
from shared.model.game_state import GameState, Result
from shared.model.board import BoardInterface
from shared.model.position import Position
from shared.engine.game_engine import BoardRepositoryInterface, GameStateRepositoryInterface, GameEngine
from shared.engine.engine_interfaces import InputSourceInterface
from shared.engine.input_commands import ClickCommand, GameCommand, RightClickCommand
from shared.io.board_parser import BoardParser
from shared.io.board_validator import BoardValidator
from shared.io.game_history_store import GameHistoryStore, SavedGame
from shared.io.moves_log import MoveLogEntry, MovesLog
from shared.realtime.arbiter_interfaces import RealTimeArbiterInterface
from shared.view.game_snapshot import GameSnapshot
from shared.view.snapshot_builder import SnapshotBuilder


class GameService:
    """Thin orchestrator and single application entry point.

    parse -> validate -> build board -> execute commands, plus the query side
    (snapshot/moves/history) the UI renders each frame. Optional collaborators
    (arbiter, moves_log, history_store) are only required for the query/history
    methods; the pure execute() path used by text tests works without them.
    """

    def __init__(
        self,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        parser: BoardParser,
        validator: BoardValidator,
        engine: GameEngine,
        bot: InputSourceInterface = None,
        config: GameConfig = None,
        arbiter: RealTimeArbiterInterface = None,
        moves_log: MovesLog = None,
        history_store: GameHistoryStore = None,
        event_bus: EventBus = None,
    ) -> None:
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._parser = parser
        self._validator = validator
        self._engine = engine
        self._bot = bot
        self._config = config or GameConfig()
        self._arbiter = arbiter
        self._moves_log = moves_log
        self._history_store = history_store
        self._event_bus = event_bus

        self._snapshot_builder: Optional[SnapshotBuilder] = None
        if arbiter is not None:
            self._snapshot_builder = SnapshotBuilder(engine=engine, arbiter=arbiter, config=self._config)

    def execute(self, input_lines: List[str]) -> Result:
        raw_board, commands = self._parser.parse(input_lines)
        if not raw_board:
            return Result.ok(None)

        init_result = self._build_and_store_board(raw_board)
        if not init_result.is_ok:
            return init_result

        return self.execute_commands(commands)

    def init_game(self, board_lines: List[str]) -> Result:
        """Parse *board_lines*, validate them, and install the starting board.

        Accepts either a bare board block or a full script (a leading
        ``Board:`` header and any ``Commands:`` are tolerated); only the board
        is used here — commands are the caller's job via execute_command().
        """
        raw_board, _ = self._parser.parse(board_lines)
        if not raw_board:
            return Result.fail(consts.ERROR_NO_BOARD_FOUND)
        return self._build_and_store_board(raw_board)

    def execute_command(self, command: GameCommand) -> Result:
        """Forward a single typed command to the engine, then let the bot react."""
        self._engine.execute_command(command)
        self._trigger_bot_reaction_if_active()
        return Result.ok(None)

    def execute_commands(self, commands: List[GameCommand]) -> Result:
        """Execute *commands* in order, stopping at the first failure."""
        for command in commands:
            result = self.execute_command(command)
            if not result.is_ok:
                return result
        return Result.ok(None)

    def click(self, row: int, col: int) -> Result:
        """Click board cell (row, col)."""
        return self.execute_command(ClickCommand(pos=Position(row, col)))

    def right_click(self, row: int, col: int) -> Result:
        """Right-click (jump-in-place) board cell (row, col)."""
        return self.execute_command(RightClickCommand(pos=Position(row, col)))

    def request_move(self, source: Position, target: Position) -> Result:
        """Move the piece on *source* to *target*, in board cells.

        What click() would take two calls to express, for callers that already
        hold both endpoints — a decoded network move frame, or a UI that
        tracks its own selection. Exists so those callers stay on this facade
        instead of reaching through to GameEngine.request_move.
        """
        self._engine.request_move(source, target)
        self._trigger_bot_reaction_if_active()
        return Result.ok(None)

    def advance_clock(self, ms: int) -> Result:
        """Advance the simulation clock and resolve pending motions."""
        self._engine.advance_clock(ms)
        return Result.ok(None)

    def update_preferences(self, ms_per_square: int, cooldown_ms: int) -> Result:
        """Apply new movement-speed / cooldown preferences to the running arbiter."""
        if self._arbiter is not None:
            self._arbiter.update_preferences(ms_per_square, cooldown_ms)
        return Result.ok(None)

    def get_snapshot(self) -> Optional[GameSnapshot]:
        """Build the read-only render DTO for the current board/state.

        Returns None before a board has been installed. Requires an arbiter
        to have been wired in (see build_realtime_service); the pure text-test
        service does not construct snapshots.
        """
        if self._snapshot_builder is None:
            raise RuntimeError("get_snapshot() requires a service built with an arbiter")
        board = self._board_repo.get_board()
        if board is None:
            return None
        state = self._state_repo.get_state()
        return self._snapshot_builder.build(board, state)

    def subscribe(self, observer: Observer, *event_types: Type[Event]) -> None:
        """Register *observer* for domain events, optionally narrowed to *event_types*.

        With no event types the observer receives everything published. This is
        how the UI attaches itself to the simulation: the engine publishes into
        a bus that knows nothing about who is listening.
        """
        self._require_event_bus()
        self._event_bus.subscribe(observer, *event_types)

    def unsubscribe(self, observer: Observer) -> None:
        self._require_event_bus()
        self._event_bus.unsubscribe(observer)

    def get_moves(self) -> List[MoveLogEntry]:
        """Every resolved move so far, oldest first."""
        if self._moves_log is None:
            return []
        return self._moves_log.entries()

    @property
    def cell_size_px(self) -> int:
        return self._config.cell_size_px

    def save_history(
        self,
        save_name: str,
        white_name: str,
        black_name: str,
        winner: Optional[str],
    ) -> str:
        """Persist the moves-so-far to disk; returns the written file path.

        The current speed/cooldown settings travel with the moves: a move's
        logged timestamp is its arrival, which only means something to a reader
        that knows how fast pieces were travelling when it was recorded.
        """
        self._require_history()
        return self._history_store.save(
            save_name,
            white_name,
            black_name,
            winner,
            self._moves_log,
            speed_ms=self._config.ms_per_square,
            cooldown_ms=self._config.cooldown_duration_ms,
        )

    def list_saves(self) -> List[str]:
        self._require_history()
        return self._history_store.list_saves()

    def load_saved_game(self, file_name: str) -> SavedGame:
        self._require_history()
        return self._history_store.load(file_name)

    def _build_and_store_board(self, raw_board) -> Result:
        validation = self._validator.validate_and_build(raw_board)
        if not validation.is_ok:
            return Result.fail(validation.error)

        board = validation.value
        self._board_repo.save_board(board)
        self._state_repo.save_state(GameState())
        self._adjust_pawn_rules_for_board_height(board)
        self._announce_game_started(board)
        return Result.ok(None)

    def _announce_game_started(self, board: BoardInterface) -> None:
        """Tell subscribers a fresh board is in play so they can clear derived state.

        Installing a board resets the clock and the game state, which would
        otherwise leave observers (score totals, capture animations) holding
        totals from the previous game.
        """
        if self._event_bus is None:
            return
        self._event_bus.publish(GameStartedEvent(at_ms=0, rows=board.rows, cols=board.cols))

    def _require_history(self) -> None:
        if self._history_store is None:
            raise RuntimeError("This service was built without a history_store")

    def _require_event_bus(self) -> None:
        if self._event_bus is None:
            raise RuntimeError("This service was built without an event_bus")

    def _adjust_pawn_rules_for_board_height(self, board: BoardInterface) -> None:
        """Re-base each player's pawn start rows and promotion rank on *board*'s height.

        Text-test boards are routinely shorter than 8x8, where the standard
        rows would put pawns off the board or make promotion unreachable.
        """
        if not self._config:
            return
        self._config.board_rows = board.rows
        self._config.board_cols = board.cols

        if board.rows == consts.DEFAULT_BOARD_ROWS:
            self._apply_pawn_rules(consts.PLAYER_W_COLOR, consts.PLAYER_W_PAWN_START_ROWS,
                                   consts.PLAYER_W_PAWN_PROMOTION_RANK)
            self._apply_pawn_rules(consts.PLAYER_B_COLOR, consts.PLAYER_B_PAWN_START_ROWS,
                                   consts.PLAYER_B_PAWN_PROMOTION_RANK)
            return

        back_rank = board.rows - consts.SHORT_BOARD_W_PAWN_START_ROW_OFFSET
        self._apply_pawn_rules(consts.PLAYER_W_COLOR, [back_rank], consts.FIRST_ROW_INDEX)
        self._apply_pawn_rules(consts.PLAYER_B_COLOR, consts.SHORT_BOARD_B_PAWN_START_ROWS,
                               board.rows - consts.SHORT_BOARD_B_PROMOTION_RANK_OFFSET)

    def _apply_pawn_rules(self, color: str, start_rows: List[int], promotion_rank: int) -> None:
        player = self._config.get_player(color)
        if player is None:
            return
        player.pawn_start_rows = list(start_rows)
        player.promotion_rank = promotion_rank

    def _trigger_bot_reaction_if_active(self) -> None:
        if getattr(self, "_in_bot_reaction", False):
            return
        if self._bot and not self._state_repo.get_state().game_over:
            self._in_bot_reaction = True
            try:
                bot_cmds = self._bot.get_next_commands()
                for b_cmd in bot_cmds:
                    self._engine.execute_command(b_cmd)
            finally:
                self._in_bot_reaction = False

