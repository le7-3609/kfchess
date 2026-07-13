"""Bootstrap — wires all layers together and runs the game from stdin.

This is the composition root for the kungfu_chess package.
"""

import sys

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
from kungfu_chess.rules.rule_engine import PathChecker
from kungfu_chess.realtime.real_time_arbiter import RealTimeArbiter, ChebyshevDistanceDuration, InstantMovementDuration
from kungfu_chess.engine.game_engine import GameEngine, MoveEventPublisher, GamePlayStateFactory
from kungfu_chess.io.board_parser import BoardParser
from kungfu_chess.io.board_printer import BoardPrinter
from kungfu_chess.io.board_validator import BoardValidator
from kungfu_chess.io.replay import ReplayWriter, ReplayEngineDecorator
from kungfu_chess.input.bot import RandomBotInputSource
from kungfu_chess.input.board_mapper import BoardMapper
from kungfu_chess.repos import _InMemoryBoardRepo, _InMemoryStateRepo
from kungfu_chess.service import GameService


def _build_core(config: GameConfig, require_kings: bool, duration_strategy):
    """Wire the common repo/parser/validator/printer/publisher/validators/engine stack.

    Shared by build_service() and build_realtime_service(); they differ only in
    duration_strategy and in build_realtime_service()'s extra replay/bot wiring.
    """
    board_repo = _InMemoryBoardRepo()
    state_repo = _InMemoryStateRepo()
    parser = BoardParser()
    validator = BoardValidator(require_kings=require_kings)
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
    path_checker = PathChecker(move_validator_factory, config)
    promotion_strategy = StandardPawnPromotion()
    game_play_state_factory = GamePlayStateFactory()
    board_mapper = BoardMapper(config.cell_size_px)

    arbiter = RealTimeArbiter(
        duration_strategy=duration_strategy,
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
        board_mapper=board_mapper,
    )

    return board_repo, state_repo, parser, validator, move_validator_factory, path_checker, arbiter, engine


def build_service(config: GameConfig = None, require_kings: bool = True) -> GameService:
    """Construct and wire a fully functional GameService."""
    if config is None:
        config = GameConfig()

    board_repo, state_repo, parser, validator, _move_validator_factory, _path_checker, _arbiter, engine = _build_core(
        config, require_kings, InstantMovementDuration()
    )

    return GameService(
        board_repo=board_repo,
        state_repo=state_repo,
        parser=parser,
        validator=validator,
        engine=engine,
        config=config,
    )


def build_realtime_service(
    config: GameConfig = None,
    ms_per_square: int = None,
    replay_file: str = None,
    bot_color: str = None,
    require_kings: bool = True
) -> GameService:
    """Construct a GameService with ChebyshevDistanceDuration for real-time movement.

    Use this when you want pieces to travel over time (the full Kung Fu Chess experience).
    Use ``build_service()`` for instant-movement tests.
    """
    if config is None:
        config = GameConfig()
    if ms_per_square is None:
        ms_per_square = config.ms_per_square

    board_repo, state_repo, parser, validator, move_validator_factory, path_checker, arbiter, engine = _build_core(
        config, require_kings, ChebyshevDistanceDuration(ms_per_square=ms_per_square)

    )

    if replay_file:
        writer = ReplayWriter(replay_file)
        engine = ReplayEngineDecorator(engine, writer)

    bot = None
    if bot_color:
        from kungfu_chess.rules.rule_engine import ThreatValidator, EndgameValidator
        threat_validator = ThreatValidator(move_validator_factory, path_checker, config)
        endgame_validator = EndgameValidator(
            move_validator_factory=move_validator_factory,
            path_checker=path_checker,
            movement_manager=arbiter,
            threat_validator=threat_validator,
            config=config,
        )
        bot = RandomBotInputSource(
            color=bot_color,
            board_repo=board_repo,
            state_repo=state_repo,
            endgame_validator=endgame_validator,
            config=config,
        )

    return GameService(
        board_repo=board_repo,
        state_repo=state_repo,
        parser=parser,
        validator=validator,
        engine=engine,
        bot=bot,
        config=config,
    )


def bootstrap() -> None:
    """Entry point: read from stdin and run the game engine with real-time movement."""
    # To enable replay or bot in normal run, you can pass arguments to build_realtime_service
    # e.g., replay_file="game.kfr", bot_color="b"
    service = build_realtime_service()
    input_lines = sys.stdin.readlines()
    result = service.execute(input_lines)
    if not result.is_ok:
        print(f"ERROR {result.error}")
