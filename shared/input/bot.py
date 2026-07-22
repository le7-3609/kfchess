"""Bot input sources (Layer 6 / Input).

Acts as an automated player issuing commands to the engine. Belongs in the
input layer because it simulates user input without owning the rules or board:
legality comes only from `EndgameValidator` (rules), the pick comes from a
`BotStrategyInterface` (policy), and this module is the gatekeeper that refuses
to execute anything the rules did not sanction.

Two collaborating input sources live here:
  - StrategyBotInputSource — resolves legal moves, delegates the choice, and
    validates the answer before turning it into a command.
  - PacedBotInputSource — a decorator that lets the inner source act only once
    per interval on the *simulation* clock, so a clock-driven bot moves at a
    human pace instead of on every tick.
"""

import logging
from typing import List, Optional

from shared.config import consts
from shared.model.position import Position
from shared.rules.endgame_validator import EndgameValidator
from shared.realtime.arbiter_interfaces import RealTimeArbiterInterface
from shared.engine.game_engine import BoardRepositoryInterface, GameStateRepositoryInterface
from shared.engine.engine_interfaces import InputSourceInterface
from shared.engine.input_commands import GameCommand, RequestMoveCommand
from shared.input.bot_strategy import BotStrategyInterface, RandomMoveStrategy

_LOGGER = logging.getLogger(__name__)


class StrategyBotInputSource(InputSourceInterface):
    """A bot that plays legal moves chosen by a pluggable strategy.

    The division of labour is strict: the rules layer says what is legal, the
    strategy says which legal move to play, and this source gatekeeps the
    answer. A strategy that returns None (no decision yet) or a move outside the
    legal set yields no command — the illegal suggestion is dropped, never run.
    """

    def __init__(
        self,
        color: str,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        endgame_validator: EndgameValidator,
        strategy: BotStrategyInterface,
        arbiter: Optional[RealTimeArbiterInterface] = None,
    ) -> None:
        self._color = color
        self._board_repo = board_repo
        self._state_repo = state_repo
        self._endgame_validator = endgame_validator
        self._strategy = strategy
        self._arbiter = arbiter

    def get_next_commands(self) -> List[GameCommand]:
        board = self._board_repo.get_board()
        if board is None:
            return []
        state = self._state_repo.get_state()
        if state.game_over:
            return []

        legal_moves = self._endgame_validator.get_legal_moves(board, state, self._color)
        if not legal_moves:
            return []

        # The strategy scores moves against the board those moves were generated
        # on: the effective board, with in-flight pieces overlaid at their
        # current squares, so a capture target reads as what is really there now.
        effective_board = board
        if self._arbiter is not None:
            effective_board = self._arbiter.get_effective_board(board, state, state.clock_ms)

        move = self._strategy.choose_move(legal_moves, effective_board, state)
        if move is None:
            return []
        if move not in legal_moves:
            _LOGGER.warning("Bot strategy proposed an illegal move %s; discarding.", move)
            return []

        source, target = move
        return [RequestMoveCommand(source=source, target=target)]


class RandomBotInputSource(StrategyBotInputSource):
    """Backward-compatible bot that plays a random legal move.

    Preserved as the pre-strategy constructor so existing callers (the server's
    build_random_bot) keep working; it is simply StrategyBotInputSource wired to
    RandomMoveStrategy.
    """

    def __init__(
        self,
        color: str,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        endgame_validator: EndgameValidator,
        arbiter: Optional[RealTimeArbiterInterface] = None,
    ) -> None:
        super().__init__(
            color, board_repo, state_repo, endgame_validator, RandomMoveStrategy(), arbiter
        )


class PacedBotInputSource(InputSourceInterface):
    """Rate-limits an inner input source to at most one batch per interval.

    Reads the simulation clock (GameState.clock_ms) rather than wall time: the
    game clock is the only clock the engine has and it never flows backward, so
    pacing stays deterministic and identical across frame rates. Crucially, the
    interval is checked *before* the inner source runs, so the expensive
    legal-move scan is skipped on every tick that is too soon to move — which is
    what makes it cheap to poll the bot on every advance_clock.
    """

    def __init__(
        self,
        inner: InputSourceInterface,
        state_repo: GameStateRepositoryInterface,
        move_interval_ms: int,
        empty_retry_ms: int = consts.BOT_EMPTY_RETRY_MS,
    ) -> None:
        if move_interval_ms <= 0:
            raise ValueError("move_interval_ms must be positive")
        if empty_retry_ms <= 0:
            raise ValueError("empty_retry_ms must be positive")
        self._inner = inner
        self._state_repo = state_repo
        self._move_interval_ms = move_interval_ms
        self._empty_retry_ms = empty_retry_ms
        self._last_issued_ms: Optional[int] = None

    def get_next_commands(self) -> List[GameCommand]:
        now_ms = self._state_repo.get_state().clock_ms

        # First poll establishes the baseline the interval is measured from, so
        # the bot waits a full interval before its opening move rather than
        # firing the instant the game starts.
        if self._last_issued_ms is None:
            self._last_issued_ms = now_ms
            return []

        if now_ms - self._last_issued_ms < self._move_interval_ms:
            return []

        commands = self._inner.get_next_commands()
        if commands:
            self._last_issued_ms = now_ms
        else:
            self._defer_retry(now_ms)
        return commands

    def _defer_retry(self, now_ms: int) -> None:
        """Push the next attempt out by the retry backoff instead of the next tick.

        An empty answer means the inner source is waiting on something — an LLM
        reply still in flight, or a position with no legal move yet. Without a
        backoff, every subsequent advance_clock tick would rerun the expensive
        legal-move scan until the wait ends, which is exactly the per-tick cost
        this pacer exists to avoid.
        """
        backoff_ms = min(self._empty_retry_ms, self._move_interval_ms)
        self._last_issued_ms = now_ms - self._move_interval_ms + backoff_ms
