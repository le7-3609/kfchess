"""GameService — the single application-facade the UI and text tests both drive.

This is the one boundary the outside world talks to. It owns parse/validate/
build/execute wiring, and exposes two kinds of operations:

  - Commands (mutate): init_game(), execute_command(), click(), right_click(),
    advance_clock(), plus history save/load.
  - Queries (read-only): get_snapshot(), get_moves(), list_saves(),
    load_saved_game(), game_over state.

Callers (the tk UI, the script runner, bots) never reach past this facade to
the GameEngine, the board/state repositories, or the arbiter directly.

It is also the subscription point for the domain EventBus: observers register
through subscribe() here rather than being handed the engine's bus, so "who
may listen" stays a property of the facade like everything else.
"""

from typing import List, Optional, Type

from kungfu_chess.config.game_config import GameConfig
from kungfu_chess.events import Event, EventBus, GameStartedEvent, Observer
from kungfu_chess.model.game_state import GameState, Result
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.engine.game_engine import BoardRepositoryInterface, GameStateRepositoryInterface, GameEngine
from kungfu_chess.engine.engine_interfaces import InputSourceInterface
from kungfu_chess.io.board_parser import BoardParser
from kungfu_chess.io.board_validator import BoardValidator
from kungfu_chess.io.game_history_store import GameHistoryStore, SavedGame
from kungfu_chess.io.moves_log import MoveLogEntry, MovesLog
from kungfu_chess.realtime.arbiter_interfaces import RealTimeArbiterInterface
from kungfu_chess.view.game_snapshot import GameSnapshot
from kungfu_chess.view.snapshot_builder import SnapshotBuilder


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

        for cmd in commands:
            self._engine.execute_command(cmd)
            self._trigger_bot_reaction_if_active()

        return Result.ok(None)

    def init_game(self, board_lines: List[str]) -> Result:
        """Parse *board_lines*, validate them, and install the starting board.

        Accepts either a bare board block or a full script (a leading
        ``Board:`` header and any ``Commands:`` are tolerated); only the board
        is used here — commands are the caller's job via execute_command().
        """
        raw_board, _ = self._parser.parse(board_lines)
        if not raw_board:
            return Result.fail("No board found in input")
        return self._build_and_store_board(raw_board)

    def execute_command(self, command: str) -> Result:
        """Forward a single DSL command (click/right_click/wait/print board)."""
        self._engine.execute_command(command)
        self._trigger_bot_reaction_if_active()
        return Result.ok(None)

    def click(self, row: int, col: int) -> Result:
        """Click board cell (row, col). Same endpoint as ``click x y``, but in
        cell coordinates so callers need not know the pixel cell size."""
        return self.execute_command(f"click {self._cell_to_px(col)} {self._cell_to_px(row)}")

    def right_click(self, row: int, col: int) -> Result:
        """Right-click (jump-in-place) board cell (row, col), in cell coordinates."""
        return self.execute_command(f"right_click {self._cell_to_px(col)} {self._cell_to_px(row)}")

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

    def _cell_to_px(self, cell: int) -> int:
        """Center-of-cell pixel coordinate for the engine's pixel mapper."""
        size = self._config.cell_size_px
        return cell * size + size // 2

    def _require_history(self) -> None:
        if self._history_store is None:
            raise RuntimeError("This service was built without a history_store")

    def _require_event_bus(self) -> None:
        if self._event_bus is None:
            raise RuntimeError("This service was built without an event_bus")

    def _adjust_pawn_rules_for_board_height(self, board: BoardInterface) -> None:
        if not self._config:
            return
        self._config.board_rows = board.rows
        self._config.board_cols = board.cols
        w_player = self._config.get_player("w")
        b_player = self._config.get_player("b")
        if board.rows == 8:
            if w_player:
                w_player.pawn_start_rows = [6]
                w_player.promotion_rank = 0
            if b_player:
                b_player.pawn_start_rows = [1]
                b_player.promotion_rank = 7
        else:
            if w_player:
                w_player.pawn_start_rows = [board.rows - 1]
                w_player.promotion_rank = 0
            if b_player:
                b_player.pawn_start_rows = [0]
                b_player.promotion_rank = board.rows - 1

    def _trigger_bot_reaction_if_active(self) -> None:
        if self._bot and not self._state_repo.get_state().game_over:
            bot_cmds = self._bot.get_next_commands()
            for b_cmd in bot_cmds:
                self._engine.execute_command(b_cmd)
