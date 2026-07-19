"""Bootstrap — wires all layers together and runs the game from stdin.

This is the composition root for the kungfu_chess package.
"""

import sys
from dataclasses import dataclass

from kungfu_chess.config import consts
from kungfu_chess.config.game_config import GameConfig
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
from kungfu_chess.events import EventBus, GameStartedEvent, PieceCapturedEvent, PieceMovedEvent
from kungfu_chess.rules.rule_engine import PathChecker
from kungfu_chess.realtime.real_time_arbiter import RealTimeArbiter, ChebyshevDistanceDuration, InstantMovementDuration
from kungfu_chess.engine.game_engine import (
    GameEngine,
    GameEngineDependencies,
    GamePlayStateFactory,
)
from kungfu_chess.scoring import MaterialScoreTracker
from kungfu_chess.io.board_parser import BoardParser
from kungfu_chess.io.board_printer import BoardPrinter
from kungfu_chess.io.board_validator import BoardValidator
from kungfu_chess.io.game_history_store import GameHistoryStore
from kungfu_chess.io.moves_log import MovesLog
from kungfu_chess.io.replay import ReplayWriter, ReplayEngineDecorator
from kungfu_chess.input.board_mapper import BoardMapper
from kungfu_chess.repos import _InMemoryBoardRepo, _InMemoryStateRepo
from kungfu_chess.service import GameService


@dataclass
class CoreComponents:
    """Output of build_core(): the shared wiring used to build a GameService.

    Also exposes everything bot_factory.build_random_bot() needs, so bot
    construction can happen outside this composition root without duplicating
    any wiring.
    """
    board_repo: _InMemoryBoardRepo
    state_repo: _InMemoryStateRepo
    parser: BoardParser
    validator: BoardValidator
    move_validator_factory: MoveValidatorFactory
    path_checker: PathChecker
    arbiter: RealTimeArbiter
    engine: GameEngine
    event_bus: EventBus
    score_tracker: MaterialScoreTracker


def _build_move_validator_factory(config: GameConfig) -> MoveValidatorFactory:
    """Assemble the per-piece-type move validators into a lookup factory."""
    return MoveValidatorFactory(validators={
        consts.PIECE_KING: KingMoveValidator(),
        consts.PIECE_QUEEN: QueenMoveValidator(),
        consts.PIECE_ROOK: RookMoveValidator(),
        consts.PIECE_BISHOP: BishopMoveValidator(),
        consts.PIECE_KNIGHT: KnightMoveValidator(),
        consts.PIECE_PAWN: PawnMoveValidator(config=config),
    })


def _build_arbiter(
    config: GameConfig,
    duration_strategy,
    path_checker: PathChecker,
    event_bus: EventBus,
) -> RealTimeArbiter:
    """Build the arbiter that moves pieces over time under *duration_strategy*."""
    return RealTimeArbiter(
        duration_strategy=duration_strategy,
        path_checker=path_checker,
        config=config,
        promotion_strategy=StandardPawnPromotion(),
        event_bus=event_bus,
    )


def _build_score_tracker(event_bus: EventBus) -> MaterialScoreTracker:
    """Build the material-score tracker and subscribe it to the events it derives from."""
    tracker = MaterialScoreTracker(event_bus)
    event_bus.subscribe(tracker, PieceCapturedEvent, GameStartedEvent)
    return tracker


def _build_engine(
    config: GameConfig,
    board_repo: _InMemoryBoardRepo,
    state_repo: _InMemoryStateRepo,
    move_validator_factory: MoveValidatorFactory,
    path_checker: PathChecker,
    arbiter: RealTimeArbiter,
    event_bus: EventBus,
) -> GameEngine:
    """Build the GameEngine over the already-constructed rule collaborators."""
    return GameEngine(GameEngineDependencies(
        board_repo=board_repo,
        state_repo=state_repo,
        printer=BoardPrinter(),
        move_validator_factory=move_validator_factory,
        event_bus=event_bus,
        path_checker=path_checker,
        config=config,
        arbiter=arbiter,
        game_play_state_factory=GamePlayStateFactory(),
        board_mapper=BoardMapper(config.cell_size_px),
    ))


def build_core(config: GameConfig, require_kings: bool, duration_strategy) -> CoreComponents:
    """Wire the repo/parser/validator/publisher/arbiter/engine stack shared by every service.

    Takes the game *config*, whether boards must contain both kings, and the
    *duration_strategy* deciding how long moves take. Returns the constructed
    components so callers can build a GameService — or, like bot_factory, wire
    extra collaborators against these same instances.
    """
    board_repo = _InMemoryBoardRepo()
    state_repo = _InMemoryStateRepo()
    event_bus = EventBus()

    move_validator_factory = _build_move_validator_factory(config)
    path_checker = PathChecker(move_validator_factory, config)
    arbiter = _build_arbiter(config, duration_strategy, path_checker, event_bus)
    engine = _build_engine(
        config, board_repo, state_repo, move_validator_factory, path_checker, arbiter, event_bus
    )

    return CoreComponents(
        board_repo=board_repo,
        state_repo=state_repo,
        parser=BoardParser(),
        validator=BoardValidator(require_kings=require_kings),
        move_validator_factory=move_validator_factory,
        path_checker=path_checker,
        arbiter=arbiter,
        engine=engine,
        event_bus=event_bus,
        score_tracker=_build_score_tracker(event_bus),
    )


def build_service(config: GameConfig = None, require_kings: bool = True) -> GameService:
    """Construct a GameService whose pieces move instantly, for tests and scripted runs.

    Defaults *config* to a standard GameConfig. See build_realtime_service()
    for pieces that travel over time.
    """
    if config is None:
        config = GameConfig()

    core = build_core(config, require_kings, InstantMovementDuration())

    return GameService(
        board_repo=core.board_repo,
        state_repo=core.state_repo,
        parser=core.parser,
        validator=core.validator,
        engine=core.engine,
        config=config,
        arbiter=core.arbiter,
        event_bus=core.event_bus,
    )


def _decorate_with_replay(engine: GameEngine, replay_file: str = None) -> GameEngine:
    """Wrap *engine* so commands are recorded to *replay_file*, or return it unchanged."""
    if not replay_file:
        return engine
    return ReplayEngineDecorator(engine, ReplayWriter(replay_file))


def _build_subscribed_moves_log(core: CoreComponents) -> MovesLog:
    """Build a MovesLog and subscribe it to the move events it records."""
    moves_log = MovesLog()
    core.event_bus.subscribe(moves_log, PieceMovedEvent)
    return moves_log


def build_realtime_service(
    config: GameConfig = None,
    ms_per_square: int = None,
    replay_file: str = None,
    require_kings: bool = True
) -> GameService:
    """Construct a GameService with ChebyshevDistanceDuration for real-time movement.

    Use this when you want pieces to travel over time (the full Kung Fu Chess experience).
    Use ``build_service()`` for instant-movement tests.

    Does not support bots: a bot needs the same board_repo/arbiter/etc.
    instances wired here, so it cannot be constructed by the caller ahead of
    time and injected as a parameter. Callers that want a bot should use
    bot_factory.build_bot_service() instead, which composes build_core() with
    bot construction directly.
    """
    if config is None:
        config = GameConfig()
    if ms_per_square is None:
        ms_per_square = config.ms_per_square
    # Mirror the caller's override back onto the config so config.ms_per_square
    # always names the speed the duration strategy is actually running at.
    config.ms_per_square = ms_per_square

    core = build_core(config, require_kings, ChebyshevDistanceDuration(ms_per_square=ms_per_square))
    engine = _decorate_with_replay(core.engine, replay_file)
    moves_log = _build_subscribed_moves_log(core)

    return GameService(
        board_repo=core.board_repo,
        state_repo=core.state_repo,
        parser=core.parser,
        validator=core.validator,
        engine=engine,
        config=config,
        arbiter=core.arbiter,
        moves_log=moves_log,
        history_store=GameHistoryStore(),
        event_bus=core.event_bus,
    )


def bootstrap() -> None:
    """Entry point: read commands from stdin and run them with real-time movement.

    Prints "ERROR <reason>" if the input is rejected. To record a replay, pass
    replay_file to build_realtime_service(); to play against a bot, use
    bot_factory.build_bot_service() instead.
    """
    service = build_realtime_service()
    input_lines = sys.stdin.readlines()
    result = service.execute(input_lines)
    if not result.is_ok:
        print(f"{consts.ERROR_OUTPUT_PREFIX} {result.error}")
