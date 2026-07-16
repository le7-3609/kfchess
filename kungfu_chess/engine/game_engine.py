"""Game engine — application-service coordination (Layer 5).

Owns: command dispatching, the resolve/game-over tick, and composing the
click/jump/castling collaborators.
Must not own: piece-specific movement logic, rendering, input parsing,
              DSL parsing, or pixel mapping.

Concrete collaborators live in:
  - engine/engine_interfaces.py (PixelMapperInterface, BoardRepositoryInterface,
    GameStateRepositoryInterface, BoardPrinterInterface, MoveEventListenerInterface,
    MoveEventPublisher)
  - engine/play_state.py     (GamePlayState, ActivePlayState, GameOverPlayState,
    GamePlayStateFactory)
  - engine/click_commands.py (ClickCommandProcessor — selection state machine)
  - engine/jump_commands.py  (JumpCommandProcessor)
  - engine/castling_commands.py (CastlingCommands)
"""

from dataclasses import dataclass
from typing import List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.game_state import GameState
from kungfu_chess.rules.piece_rules import MoveValidatorFactoryInterface
from kungfu_chess.rules.rule_engine import (
    PathCheckerInterface,
    RuleEngine,
    ThreatValidator,
    EndgameValidator,
    CastlingValidator,
    serialize_board_state,
)
from kungfu_chess.realtime.arbiter_interfaces import RealTimeArbiterInterface
from kungfu_chess.engine.engine_interfaces import (
    PixelMapperInterface,
    BoardRepositoryInterface,
    GameStateRepositoryInterface,
    BoardPrinterInterface,
    MoveEventListenerInterface,
    MoveEventPublisher,
)
from kungfu_chess.engine.play_state import (
    GamePlayState,
    ActivePlayState,
    GameOverPlayState,
    GamePlayStateFactory,
)
from kungfu_chess.engine.click_commands import ClickCommandProcessor
from kungfu_chess.engine.jump_commands import JumpCommandProcessor
from kungfu_chess.engine.castling_commands import CastlingCommands

__all__ = [
    "PixelMapperInterface",
    "BoardRepositoryInterface",
    "GameStateRepositoryInterface",
    "BoardPrinterInterface",
    "MoveEventListenerInterface",
    "MoveEventPublisher",
    "GamePlayState",
    "ActivePlayState",
    "GameOverPlayState",
    "GamePlayStateFactory",
    "GameEngineDependencies",
    "GameEngine",
]


@dataclass
class GameEngineDependencies:
    """Parameter object bundling GameEngine's collaborators.

    Required fields must be supplied by the composition root (bootstrap.py).
    Optional fields default to standard implementations inside GameEngine
    when left as None, mirroring the engine's previous constructor defaults.
    """

    board_repo: BoardRepositoryInterface
    state_repo: GameStateRepositoryInterface
    printer: BoardPrinterInterface
    move_validator_factory: MoveValidatorFactoryInterface
    move_event_publisher: MoveEventPublisher
    path_checker: PathCheckerInterface
    config: 'GameConfig'  # type: ignore[name-defined]
    board_mapper: PixelMapperInterface
    arbiter: Optional[RealTimeArbiterInterface] = None
    game_play_state_factory: Optional[GamePlayStateFactory] = None
    threat_validator: Optional[ThreatValidator] = None
    endgame_validator: Optional[EndgameValidator] = None
    castling_validator: Optional[CastlingValidator] = None


class GameEngine:
    """Application-service coordinator.

    Handles:
      - Click/jump/wait/print-board command dispatching
      - Delegating move and castling legality to piece_rules and rule_engine
        (via ClickCommandProcessor/CastlingCommands)
      - Starting legal motions via the arbiter (including castling's king+rook pair)
      - Game-over detection after each command

    Pixel-to-cell mapping is delegated to a PixelMapperInterface implementation
    (see input/board_mapper.py for the concrete BoardMapper); the engine layer
    depends only on the interface, never on the input/ package.
    Selection state (GameState.selected_pos) is owned and mutated here, not by
    the Controller — the Controller is a stateless click-to-command translator.
    """

    def __init__(self, deps: GameEngineDependencies) -> None:
        self._store_required_collaborators(deps)
        self._build_rule_collaborators(deps)
        self._build_command_processors(deps)
        self._last_checked_signature = None

    def _store_required_collaborators(self, deps: GameEngineDependencies) -> None:
        """Adopt the collaborators the composition root must always supply."""
        self._board_repo = deps.board_repo
        self._state_repo = deps.state_repo
        self._printer = deps.printer
        self._move_validator_factory = deps.move_validator_factory
        self._move_event_publisher = deps.move_event_publisher
        self._path_checker = deps.path_checker
        self._config = deps.config
        self._board_mapper = deps.board_mapper

    def _build_rule_collaborators(self, deps: GameEngineDependencies) -> None:
        """Adopt the rule-layer collaborators, constructing standard defaults for any omitted.

        Each is assigned before the next is built: the endgame and castling
        validators are constructed against the arbiter and threat validator
        resolved here, whether those came from *deps* or from a default.
        """
        self._arbiter = self._or_default(deps.arbiter, self._create_default_arbiter)
        self._rule_engine = RuleEngine(move_validator_factory=self._move_validator_factory)
        self._threat_validator = self._or_default(
            deps.threat_validator, self._create_default_threat_validator
        )
        self._endgame_validator = self._or_default(
            deps.endgame_validator, self._create_default_endgame_validator
        )
        self._castling_validator = self._or_default(
            deps.castling_validator, self._create_default_castling_validator
        )
        self._game_play_state_factory = self._or_default(
            deps.game_play_state_factory, GamePlayStateFactory
        )

    @staticmethod
    def _or_default(supplied, build_default):
        """Return *supplied*, or the result of *build_default* when nothing was supplied.

        Tests inject collaborators that may define __bool__/__len__, so presence
        is decided on None rather than truthiness.
        """
        return build_default() if supplied is None else supplied

    def _create_default_arbiter(self) -> RealTimeArbiterInterface:
        """Build an arbiter that completes every motion instantly."""
        # Imported lazily: realtime.real_time_arbiter imports this module.
        from kungfu_chess.realtime.real_time_arbiter import (
            RealTimeArbiter, InstantMovementDuration,
        )
        from kungfu_chess.rules.piece_rules import StandardPawnPromotion
        return RealTimeArbiter(
            duration_strategy=InstantMovementDuration(),
            path_checker=self._path_checker,
            config=self._config,
            promotion_strategy=StandardPawnPromotion(),
            move_event_publisher=self._move_event_publisher,
        )

    def _create_default_threat_validator(self) -> ThreatValidator:
        return ThreatValidator(
            move_validator_factory=self._move_validator_factory,
            path_checker=self._path_checker,
            config=self._config,
        )

    def _create_default_endgame_validator(self) -> EndgameValidator:
        return EndgameValidator(
            move_validator_factory=self._move_validator_factory,
            path_checker=self._path_checker,
            movement_manager=self._arbiter,
            threat_validator=self._threat_validator,
            config=self._config,
        )

    def _create_default_castling_validator(self) -> CastlingValidator:
        return CastlingValidator(threat_validator=self._threat_validator, config=self._config)

    def _build_command_processors(self, deps: GameEngineDependencies) -> None:
        """Wire the per-command processors onto the rule collaborators."""
        self._castling_commands = CastlingCommands(
            arbiter=self._arbiter,
            castling_validator=self._castling_validator,
            state_repo=self._state_repo,
            resolve_pending=self._resolve_pending,
        )
        self._jump_commands = JumpCommandProcessor(
            config=self._config, state_repo=self._state_repo, arbiter=self._arbiter
        )
        self._click_commands = ClickCommandProcessor(
            rule_engine=self._rule_engine,
            threat_validator=self._threat_validator,
            arbiter=self._arbiter,
            castling_commands=self._castling_commands,
            jump_commands=self._jump_commands,
            state_repo=self._state_repo,
            resolve_pending=self._resolve_pending,
        )

    def request_move(self, source: Position, destination: Position) -> None:
        """Attempt a move from *source* to *destination*.

        This is the Controller-facing entry point: the caller (Controller)
        already resolved pixels to cells and owns selection state itself, so
        unlike ``execute_command("click ...")`` no selection bookkeeping is
        read from or written back to GameState here beyond what the legality
        gate (RuleEngine/PathChecker/ThreatValidator) needs to run.
        """
        self._resolve_pending()
        board = self._board_repo.get_board()
        if board is None:
            return
        state = self._state_repo.get_state()
        if state.game_over:
            return
        state.selected_pos = source
        self._click_commands.handle_click(state, board, destination)

    def legal_moves_from(self, source: Position) -> List[Position]:
        """Return every legal destination for the piece at *source* right now.

        Read-only view-layer query: reuses EndgameValidator's self-check-safe
        legality gate (the same one ClickCommandProcessor uses) so a Renderer
        can highlight legal squares without duplicating any rule logic
        itself. Queries only the selected piece rather than every friendly
        piece on the board, since this runs on every render tick.
        """
        board = self._board_repo.get_board()
        if board is None:
            return []
        state = self._state_repo.get_state()
        return self._endgame_validator.get_legal_moves_for_position(board, state, source)

    def castle_rook_targets_from(self, king_pos: Position) -> List[Position]:
        """Return friendly rook squares *king_pos* may legally castle with right now."""
        board = self._board_repo.get_board()
        if board is None:
            return []
        state = self._state_repo.get_state()
        king_piece = board.get_piece(king_pos)
        if king_piece is None or king_piece.piece_type not in self._config.king_pieces:
            return []

        eff_board = self._arbiter.get_effective_board(board, state, state.clock_ms)
        targets: List[Position] = []
        for c in range(board.cols):
            rook_pos = Position(king_pos.row, c)
            rook_piece = board.get_piece(rook_pos)
            if rook_piece is None:
                continue
            if not self._castling_validator.is_castle_attempt(king_piece, rook_piece, king_pos, rook_pos):
                continue
            if self._castling_validator.get_legal_castle(eff_board, king_pos, rook_pos, king_piece) is not None:
                targets.append(rook_pos)
        return targets

    def advance_clock(self, ms: int) -> None:
        """Advance the simulation clock by *ms* and resolve pending motions.

        Public equivalent of the "wait N" text command, for callers that
        drive the clock from a real event loop (see runtime/async_runner.py)
        rather than from a scripted command stream. Unlike execute_command,
        this never parses a command string — it only performs the time
        advancement + resolve step.
        """
        if ms <= 0:
            return
        state = self._state_repo.get_state()
        state.clock_ms += ms
        self._state_repo.save_state(state)
        self._resolve_pending()

    def execute_command(self, command: str) -> None:
        """Execute a single text command against the current game state.

        Accepts "click X Y", "right_click X Y", "wait MS", and "print board".
        Anything else is ignored: command streams come from scripted text tests
        and stdin, where an unrecognised line must not abort the run.
        """
        parts = command.split()
        if not parts:
            return

        if parts[0] == "click" and len(parts) == 3:
            self._handle_click(int(parts[1]), int(parts[2]))
        elif parts[0] == "right_click" and len(parts) == 3:
            self._handle_right_click(int(parts[1]), int(parts[2]))
        elif parts[0] == "wait" and len(parts) == 2:
            self._handle_wait(int(parts[1]))
        elif command == "print board":
            self._handle_print_board()

    def _resolve_pending(self) -> None:
        """Resolve all pending motions at the current clock time.

        _check_game_end_conditions re-scans the whole board (checkmate/
        stalemate call get_legal_moves for every piece of a color, each
        candidate re-checking king safety) - expensive enough that running
        it unconditionally on every 16ms render tick, including idle ticks
        where nothing moved, dominates the tick budget. It only needs to
        re-run when something that could change its answer actually changed:
        piece positions (current_serialized) or active-cooldown membership
        (which piece.can_move()/is_checkmate's cooldown gate depend on).
        """
        board = self._board_repo.get_board()
        if board is None:
            return
        state = self._state_repo.get_state()

        if not state.position_history:
            state.position_history.append(serialize_board_state(board, state))

        cooldown_ids_before = frozenset(id(c.piece) for c in state.active_cooldowns)
        self._arbiter.resolve_movements(board, state, state.clock_ms)
        cooldown_ids_after = frozenset(id(c.piece) for c in state.active_cooldowns)

        current_serialized = serialize_board_state(board, state)
        board_changed = state.position_history[-1] != current_serialized
        if board_changed:
            state.position_history.append(current_serialized)

        signature = (current_serialized, cooldown_ids_after)
        if board_changed or cooldown_ids_after != cooldown_ids_before or signature != self._last_checked_signature:
            self._check_game_end_conditions(board, state)
            self._last_checked_signature = signature

        self._board_repo.save_board(board)
        self._state_repo.save_state(state)

    def _check_game_end_conditions(self, board: BoardInterface, state: GameState) -> None:
        """Record the first end-of-game condition that applies, if any.

        A king already off the board means the capture path has ended the game
        (or is about to), so the checkmate/stalemate scans are skipped rather
        than run against an incomplete position.
        """
        if state.game_over or not self._both_kings_present(board):
            return
        if self._check_decisive_end(board, state):
            return
        self._check_draw_end(board, state)

    def _both_kings_present(self, board: BoardInterface) -> bool:
        return (
            self._endgame_validator._has_king(board, "w")
            and self._endgame_validator._has_king(board, "b")
        )

    def _check_decisive_end(self, board: BoardInterface, state: GameState) -> bool:
        """End the game if either color is checkmated or stalemated. Returns whether it did."""
        for color in ("w", "b"):
            if self._endgame_validator.is_checkmate(board, state, color):
                self._end_game(state, "checkmate", winner=self._opponent(color))
                return True
            if self._endgame_validator.is_stalemate(board, state, color):
                self._end_game(state, "stalemate")
                return True
        return False

    def _check_draw_end(self, board: BoardInterface, state: GameState) -> bool:
        """End the game if any drawn-position rule applies. Returns whether it did."""
        draw_rules = (
            ("insufficient_material", lambda: self._endgame_validator.is_insufficient_material(board)),
            ("threefold_repetition", lambda: self._endgame_validator.is_threefold_repetition(board, state)),
            ("fifty_move_rule", lambda: self._endgame_validator.is_fifty_move_rule(board, state)),
        )
        for reason, is_drawn in draw_rules:
            if is_drawn():
                self._end_game(state, reason)
                return True
        return False

    def _end_game(self, state: GameState, reason: str, winner: Optional[str] = None) -> None:
        """Mark the game over with *reason*, leaving the winner unset for a draw."""
        state.game_over = True
        state.game_over_reason = reason
        if winner is not None:
            state.winner = winner

    @staticmethod
    def _opponent(color: str) -> str:
        return "b" if color == "w" else "w"

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

    def _handle_right_click(self, x: int, y: int) -> None:
        """Jump the piece under (x, y) in place, regardless of current selection.

        Mirrors request_move(target, target): a same-cell move is the arbiter's
        jump-in-place. Unlike _handle_click this ignores selection state, so it
        works as a direct "make this piece hop" command.
        """
        self._resolve_pending()
        board = self._board_repo.get_board()
        if board is None:
            return
        target = self._board_mapper.pixel_to_position(x, y, board)
        if target is None:
            return
        self.request_move(target, target)

    def _execute_active_click(self, target: Position) -> None:
        board = self._board_repo.get_board()
        if board is None:
            return
        state = self._state_repo.get_state()
        self._click_commands.handle_click(state, board, target)

    def _handle_wait(self, ms: int) -> None:
        self.advance_clock(ms)

    def _handle_print_board(self) -> None:
        self._resolve_pending()
        board = self._board_repo.get_board()
        if board is not None:
            self._printer.print_board(board)
