"""Bot construction — builds a bot input source and wires it into a GameService.

Kept separate from bootstrap.py so the composition root does not need to know
about bot-specific configuration details; callers that want a bot (e.g. the CLI
entry point, the offline lobby) call build_bot_service() here instead of
bootstrap.build_realtime_service().

Two seams live here:
  - build_random_bot() — the unpaced, random source the *server* seats. The
    server drives cadence itself with BotDriver, so it must not be paced here.
  - build_bot_service() — the offline path. It builds the strategy the profile
    names, then wraps it in PacedBotInputSource so the local clock-driven loop
    moves the bot at a human pace.

An LLM strategy needs the network, which shared/ may not import, so the client
constructs that strategy and passes it in via *strategy*; GREEDY and RANDOM are
resolved here from the profile's difficulty.
"""

from typing import Callable, Dict, Optional

from shared.config.game_config import GameConfig
from shared.config.bot_profile import BotDifficulty, BotProfile
from shared.realtime.real_time_arbiter import ChebyshevDistanceDuration
from shared.rules.rule_engine import ThreatValidator, EndgameValidator
from shared.io.replay import ReplayWriter, ReplayEngineDecorator
from shared.input.bot import PacedBotInputSource, RandomBotInputSource, StrategyBotInputSource
from shared.input.bot_strategy import (
    BotStrategyInterface,
    GreedyCaptureStrategy,
    RandomMoveStrategy,
)
from shared.bootstrap import build_core, CoreComponents
from shared.service import GameService


def _build_endgame_validator(core: CoreComponents, config: GameConfig) -> EndgameValidator:
    threat_validator = ThreatValidator(core.move_validator_factory, core.path_checker, config)
    return EndgameValidator(
        move_validator_factory=core.move_validator_factory,
        path_checker=core.path_checker,
        movement_manager=core.arbiter,
        threat_validator=threat_validator,
        config=config,
    )


# The difficulties shared/ can compose on its own, each mapped to the strategy
# constructor that builds it. Declared once so adding a self-contained strategy
# is one entry, not another branch. LLM is intentionally absent: it needs a
# network client the client layer owns, so callers pass that strategy in.
_STRATEGY_BUILDERS: Dict[BotDifficulty, Callable[[], BotStrategyInterface]] = {
    BotDifficulty.RANDOM: RandomMoveStrategy,
    BotDifficulty.GREEDY: GreedyCaptureStrategy,
}


def _strategy_for(difficulty: BotDifficulty) -> BotStrategyInterface:
    """Resolve the self-contained strategies shared/ can build on its own."""
    builder = _STRATEGY_BUILDERS.get(difficulty)
    if builder is None:
        raise ValueError(
            f"{difficulty} cannot be built inside shared; pass an explicit strategy instead"
        )
    return builder()


def build_random_bot(color: str, core: CoreComponents, config: GameConfig) -> RandomBotInputSource:
    """An unpaced random bot for callers that pace it themselves (the server)."""
    return RandomBotInputSource(
        color=color,
        board_repo=core.board_repo,
        state_repo=core.state_repo,
        endgame_validator=_build_endgame_validator(core, config),
        arbiter=core.arbiter,
    )


def build_paced_bot(
    color: str,
    core: CoreComponents,
    config: GameConfig,
    profile: BotProfile,
    strategy: Optional[BotStrategyInterface] = None,
) -> PacedBotInputSource:
    """Build the profile's strategy (or use the one supplied) and pace it on the game clock."""
    if strategy is None:
        strategy = _strategy_for(profile.difficulty)
    inner = StrategyBotInputSource(
        color=color,
        board_repo=core.board_repo,
        state_repo=core.state_repo,
        endgame_validator=_build_endgame_validator(core, config),
        strategy=strategy,
        arbiter=core.arbiter,
    )
    return PacedBotInputSource(inner, core.state_repo, profile.move_interval_ms)


def build_bot_service(
    bot_color: str,
    config: GameConfig = None,
    ms_per_square: int = None,
    replay_file: str = None,
    require_kings: bool = True,
    profile: BotProfile = None,
    strategy: BotStrategyInterface = None,
) -> GameService:
    if config is None:
        config = GameConfig()
    if ms_per_square is None:
        ms_per_square = config.ms_per_square
    config.ms_per_square = ms_per_square
    if profile is None:
        profile = BotProfile()

    core = build_core(config, require_kings, ChebyshevDistanceDuration(ms_per_square=ms_per_square))
    bot = build_paced_bot(bot_color, core, config, profile, strategy)

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
