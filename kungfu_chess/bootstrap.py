"""Bootstrap — wires all layers together and runs the game from stdin.

This is the composition root for the kungfu_chess package.
"""

import sys
from typing import List

from kungfu_chess.config.game_config import GameConfig
from kungfu_chess.model.game_state import GameState, Result
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.rules.piece_rules import (
    MoveValidatorFactory,
    KingMoveValidator,
    QueenMoveValidator,
    RookMoveValidator,
    BishopMoveValidator,
    KnightMoveValidator,
    PawnMoveValidator,
    StandardPawnPromotion,
)
from kungfu_chess.rules.rule_engine import PathChecker, ThreatValidator, EndgameValidator
from kungfu_chess.realtime.real_time_arbiter import RealTimeArbiter, ChebyshevDistanceDuration, InstantMovementDuration
from kungfu_chess.engine.game_engine import (
    GameEngine,
    MoveEventPublisher,
    BoardRepositoryInterface,
    GameStateRepositoryInterface,
    GamePlayStateFactory,
    BoardPrinterInterface,
)
from kungfu_chess.io.board_parser import BoardParser
from kungfu_chess.io.board_printer import BoardPrinter
from kungfu_chess.io.board_validator import BoardValidator


# ---------------------------------------------------------------------------
# In-memory repository implementations
# ---------------------------------------------------------------------------

class _InMemoryBoardRepo(BoardRepositoryInterface):
    def __init__(self) -> None:
        self._board = None

    def get_board(self):
        return self._board

    def save_board(self, board) -> None:
        self._board = board


class _InMemoryStateRepo(GameStateRepositoryInterface):
    def __init__(self) -> None:
        self._state: GameState = GameState()

    def get_state(self) -> GameState:
        return self._state

    def save_state(self, state: GameState) -> None:
        self._state = state


# ---------------------------------------------------------------------------
# GameService (thin orchestrator — parse → validate → execute commands)
# ---------------------------------------------------------------------------

class GameService:
    """Thin orchestrator: parse, validate, build board, execute commands."""

    def __init__(
        self,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        parser: BoardParser,
        validator: BoardValidator,
        engine: GameEngine,
    ) -> None:
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._parser = parser
        self._validator = validator
        self._engine = engine

    def execute(self, input_lines: List[str]) -> Result:
        raw_board, commands = self._parser.parse(input_lines)
        if not raw_board:
            return Result.ok(None)

        validation = self._validator.validate_and_build(raw_board)
        if not validation.is_ok:
            return Result.fail(validation.error)

        self._board_repo.save_board(validation.value)
        self._state_repo.save_state(GameState())

        for cmd in commands:
            self._engine.execute_command(cmd)

        return Result.ok(None)


# ---------------------------------------------------------------------------
# Public factory function
# ---------------------------------------------------------------------------

def build_service(config: GameConfig = None) -> GameService:
    """Construct and wire a fully functional GameService."""
    if config is None:
        config = GameConfig()

    board_repo = _InMemoryBoardRepo()
    state_repo = _InMemoryStateRepo()
    parser = BoardParser()
    validator = BoardValidator()
    printer = BoardPrinter()
    publisher = MoveEventPublisher()

    move_validators = {
        "K": KingMoveValidator(),
        "Q": QueenMoveValidator(),
        "R": RookMoveValidator(),
        "B": BishopMoveValidator(),
        "N": KnightMoveValidator(),
        "P": PawnMoveValidator(config=config),
    }
    move_validator_factory = MoveValidatorFactory(validators=move_validators)
    path_checker = PathChecker()
    promotion_strategy = StandardPawnPromotion()
    game_play_state_factory = GamePlayStateFactory()

    arbiter = RealTimeArbiter(
        duration_strategy=InstantMovementDuration(),
        path_checker=path_checker,
        config=config,
        promotion_strategy=promotion_strategy,
        move_event_publisher=publisher,
    )

    engine = GameEngine(
        board_repo=board_repo,
        state_repo=state_repo,
        printer=printer,
        move_validator_factory=move_validator_factory,
        move_event_publisher=publisher,
        path_checker=path_checker,
        config=config,
        arbiter=arbiter,
        game_play_state_factory=game_play_state_factory,
    )

    return GameService(
        board_repo=board_repo,
        state_repo=state_repo,
        parser=parser,
        validator=validator,
        engine=engine,
    )
def build_realtime_service(config: GameConfig = None, ms_per_square: int = None) -> GameService:
    """Construct a GameService with ChebyshevDistanceDuration for real-time movement.

    Use this when you want pieces to travel over time (the full Kung Fu Chess experience).
    Use ``build_service()`` for instant-movement tests.
    """
    if config is None:
        config = GameConfig()
    if ms_per_square is None:
        ms_per_square = config.ms_per_square

    board_repo = _InMemoryBoardRepo()
    state_repo = _InMemoryStateRepo()
    parser = BoardParser()
    validator = BoardValidator()
    printer = BoardPrinter()
    publisher = MoveEventPublisher()

    move_validators = {
        "K": KingMoveValidator(),
        "Q": QueenMoveValidator(),
        "R": RookMoveValidator(),
        "B": BishopMoveValidator(),
        "N": KnightMoveValidator(),
        "P": PawnMoveValidator(config=config),
    }
    move_validator_factory = MoveValidatorFactory(validators=move_validators)
    path_checker = PathChecker()
    promotion_strategy = StandardPawnPromotion()
    game_play_state_factory = GamePlayStateFactory()

    arbiter = RealTimeArbiter(
        duration_strategy=ChebyshevDistanceDuration(ms_per_square=ms_per_square),
        path_checker=path_checker,
        config=config,
        promotion_strategy=promotion_strategy,
        move_event_publisher=publisher,
    )

    engine = GameEngine(
        board_repo=board_repo,
        state_repo=state_repo,
        printer=printer,
        move_validator_factory=move_validator_factory,
        move_event_publisher=publisher,
        path_checker=path_checker,
        config=config,
        arbiter=arbiter,
        game_play_state_factory=game_play_state_factory,
    )

    return GameService(
        board_repo=board_repo,
        state_repo=state_repo,
        parser=parser,
        validator=validator,
        engine=engine,
    )


def bootstrap() -> None:
    """Entry point: read from stdin and run the game engine with real-time movement."""
    service = build_realtime_service()
    input_lines = sys.stdin.readlines()
    result = service.execute(input_lines)
    if not result.is_ok:
        print(f"ERROR {result.error}")
