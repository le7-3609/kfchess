"""Bot profile — how an automated opponent should play (Layer 1-2 / config).

Owns: the difficulty and pacing an offline bot match is configured with.
Must not own: strategy behaviour, move selection, or the network transport an
LLM strategy needs — those live in `input/` and `client/` respectively. This
module is pure data the composition root reads to build the right bot.
"""

from dataclasses import dataclass
from enum import Enum

from shared.config import consts


class BotDifficulty(Enum):
    """Which strategy an automated opponent picks its moves with.

    GREEDY and RANDOM are self-contained in `shared/input`. LLM delegates to a
    hosted model and therefore needs a network client, which `shared` may not
    import — the client composes that strategy (whichever provider its registry
    selects) and hands it in (see bot_factory.build_bot_service).
    """

    GREEDY = "greedy"
    RANDOM = "random"
    LLM = "llm"


@dataclass(frozen=True)
class BotProfile:
    """The knobs a lobby exposes for a bot match: how it thinks and how fast it acts.

    move_interval_ms is measured on the simulation clock — the only clock the
    engine has — so the same profile plays identically regardless of frame rate.
    """

    difficulty: BotDifficulty = BotDifficulty.GREEDY
    move_interval_ms: int = consts.DEFAULT_BOT_MOVE_INTERVAL_MS
