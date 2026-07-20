"""Bot driver — pumps a bot's input source into a live room on a fixed cadence.

Owns: the clock on which an automated player issues its moves, and the
lifecycle of the background task that does so.
Must not own: move selection (that is `RandomBotInputSource`), game rules, or
network transport.

There is no turn to react to in Kung Fu Chess, so a bot cannot be driven by an
opponent's move the way `GameService._trigger_bot_reaction_if_active` drives the
scripted path. It needs its own cadence, and its commands must go through
`AsyncGameRunner.submit_command` rather than straight at the engine, so they are
drained between ticks instead of interleaving with a resolve in progress.
"""

import asyncio
import logging
from typing import Any, Callable, List, Optional

from shared.engine.input_commands import GameCommand

_LOGGER = logging.getLogger(__name__)

DEFAULT_BOT_MOVE_INTERVAL_SECONDS = 1.0


class BotDriver:
    """Issues a bot's chosen commands into a running game at a fixed interval."""

    def __init__(
        self,
        input_source: Any,
        submit_command: Callable[[GameCommand], "asyncio.Future"],
        is_game_over: Callable[[], bool],
        move_interval_seconds: float = DEFAULT_BOT_MOVE_INTERVAL_SECONDS,
        bot_name: str = "bot",
    ) -> None:
        if move_interval_seconds <= 0:
            raise ValueError("move_interval_seconds must be positive")

        self._input_source = input_source
        self._submit_command = submit_command
        self._is_game_over = is_game_over
        self._move_interval_seconds = move_interval_seconds
        self._bot_name = bot_name
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        """Start the background move loop. No-op if already running."""
        if self.running:
            return
        self._stopped.clear()
        self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        """Stop the move loop and wait for the task to unwind."""
        if self._task is None:
            return
        self._stopped.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        try:
            while not self._stopped.is_set():
                await asyncio.sleep(self._move_interval_seconds)
                if self._is_game_over():
                    return
                self._submit_next_move()
        except asyncio.CancelledError:
            pass

    def _submit_next_move(self) -> None:
        """Ask the input source for its next move and queue it for the runner.

        A failure here is contained rather than raised: an exception would kill
        the driver task outright and silently freeze the bot for the rest of the
        game, which is the exact dead-end the bot fallback exists to prevent.
        """
        try:
            commands: List[GameCommand] = self._input_source.get_next_commands()
        except Exception as exc:
            _LOGGER.warning("Bot %s failed to choose a move: %s", self._bot_name, exc)
            return

        for command in commands:
            try:
                self._submit_command(command)
            except Exception as exc:
                _LOGGER.warning("Bot %s failed to submit %r: %s", self._bot_name, command, exc)
                return
