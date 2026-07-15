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


# ---------------------------------------------------------------------------
# GameEngineDependencies
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# GameEngine
# ---------------------------------------------------------------------------

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
        board_repo = deps.board_repo
        state_repo = deps.state_repo
        move_validator_factory = deps.move_validator_factory
        move_event_publisher = deps.move_event_publisher
        path_checker = deps.path_checker
        config = deps.config
        arbiter = deps.arbiter
        game_play_state_factory = deps.game_play_state_factory
        threat_validator = deps.threat_validator
        endgame_validator = deps.endgame_validator
        castling_validator = deps.castling_validator

        self._board_repo = board_repo
        self._state_repo = state_repo
        self._printer = deps.printer
        self._move_validator_factory = move_validator_factory
        self._move_event_publisher = move_event_publisher
        self._path_checker = path_checker
        self._config = config
        self._board_mapper = deps.board_mapper

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

        rule_engine = RuleEngine(move_validator_factory=move_validator_factory)
        self._rule_engine = rule_engine

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

        if castling_validator is None:
            castling_validator = CastlingValidator(threat_validator=self._threat_validator, config=config)
        self._castling_validator = castling_validator

        self._castling_commands = CastlingCommands(
            arbiter=self._arbiter,
            castling_validator=self._castling_validator,
            state_repo=state_repo,
            resolve_pending=self._resolve_pending,
        )
        self._jump_commands = JumpCommandProcessor(config=config, state_repo=state_repo, arbiter=self._arbiter)
        self._click_commands = ClickCommandProcessor(
            rule_engine=self._rule_engine,
            threat_validator=self._threat_validator,
            arbiter=self._arbiter,
            castling_commands=self._castling_commands,
            jump_commands=self._jump_commands,
            state_repo=state_repo,
            resolve_pending=self._resolve_pending,
        )

    # ------------------------------------------------------------------
    # Public command dispatcher
    # ------------------------------------------------------------------

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

        Read-only view-layer query: reuses EndgameValidator.get_legal_moves
        (the same self-check-safe legality gate ClickCommandProcessor uses)
        so a Renderer can highlight legal squares without duplicating any
        rule logic itself.
        """
        board = self._board_repo.get_board()
        if board is None:
            return []
        state = self._state_repo.get_state()
        piece = board.get_piece(source)
        if piece is None:
            return []
        pairs = self._endgame_validator.get_legal_moves(board, state, piece.color)
        return [to for (frm, to) in pairs if frm == source]

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
        """Execute a single text command against the current game state."""
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
                state.winner = "b" if color == "w" else "w"
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
        state = self._state_repo.get_state()
        self._click_commands.handle_click(state, board, target)

    def _handle_wait(self, ms: int) -> None:
        self.advance_clock(ms)

    def _handle_print_board(self) -> None:
        self._resolve_pending()
        board = self._board_repo.get_board()
        if board is not None:
            self._printer.print_board(board)
