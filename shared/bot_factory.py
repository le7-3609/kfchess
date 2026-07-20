"""Bot construction — builds a RandomBotInputSource and wires it into a GameService.

Kept separate from bootstrap.py so the composition root does not need to
know about bot-specific configuration details; callers that want a bot
(e.g. the CLI entry point) call build_bot_service() here instead of
bootstrap.build_realtime_service().
"""

from shared.config.game_config import GameConfig
from shared.realtime.real_time_arbiter import ChebyshevDistanceDuration
from shared.rules.rule_engine import ThreatValidator, EndgameValidator
from shared.io.replay import ReplayWriter, ReplayEngineDecorator
from shared.input.bot import RandomBotInputSource
from shared.bootstrap import build_core, CoreComponents
from shared.service import GameService


def build_random_bot(color: str, core: CoreComponents, config: GameConfig) -> RandomBotInputSource:
    threat_validator = ThreatValidator(core.move_validator_factory, core.path_checker, config)
    endgame_validator = EndgameValidator(
        move_validator_factory=core.move_validator_factory,
        path_checker=core.path_checker,
        movement_manager=core.arbiter,
        threat_validator=threat_validator,
        config=config,
    )
    return RandomBotInputSource(
        color=color,
        board_repo=core.board_repo,
        state_repo=core.state_repo,
        endgame_validator=endgame_validator,
    )


def build_bot_service(
    bot_color: str,
    config: GameConfig = None,
    ms_per_square: int = None,
    replay_file: str = None,
    require_kings: bool = True,
) -> GameService:
    if config is None:
        config = GameConfig()
    if ms_per_square is None:
        ms_per_square = config.ms_per_square
    config.ms_per_square = ms_per_square

    core = build_core(config, require_kings, ChebyshevDistanceDuration(ms_per_square=ms_per_square))
    bot = build_random_bot(bot_color, core, config)

    engine = core.engine
    if replay_file:
        writer = ReplayWriter(replay_file)
        engine = ReplayEngineDecorator(engine, writer)

    return GameService(
        board_repo=core.board_repo,
        state_repo=core.state_repo,
        parser=core.parser,
        validator=core.validator,
        engine=engine,
        bot=bot,
        config=config,
        arbiter=core.arbiter,
        event_bus=core.event_bus,
    )
