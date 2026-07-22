"""AsyncGameRunner — real-time asyncio tick loop for GameEngine (Layer 9).

The synchronous path (`GameEngine.execute_command(WaitCommand(n))`, used by
ScriptRunner/text tests) advances `state.clock_ms` by a fixed amount and
immediately resolves in-line — good for deterministic tests, wrong for a
live server where wall-clock time passes between client messages.

This module keeps that synchronous path untouched and adds a second,
decoupled way to advance the same clock: a background asyncio task that
wakes up every `tick_interval` seconds and calls `GameEngine.advance_clock`
with the elapsed wall-clock time (which advances `state.clock_ms` and
resolves movements) — while commands (clicks, selections) submitted
concurrently from network handlers are queued and drained once per tick,
so they never interleave with a resolve in progress.
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, List, Optional

from shared.config.consts import MS_PER_SECOND
from shared.engine.game_engine import GameEngine
from shared.engine.input_commands import GameCommand, WaitCommand

DEFAULT_TICK_RATE_HZ = 20.0


@dataclass
class QueuedCommand:
    """A single command awaiting application on the next tick."""

    command: GameCommand
    future: Optional["asyncio.Future"] = field(default=None, repr=False)


class AsyncGameRunner:
    """Runs a GameEngine in real time using an asyncio event loop.

    Usage::

        runner = AsyncGameRunner(engine, tick_rate_hz=20)
        await runner.start()
        ...
        runner.submit_command(ClickCommand(Position(1, 2)))  # thread/coroutine-safe enqueue
        ...
        await runner.stop()

    Time advancement is fully decoupled from the synchronous WaitCommand
    path: this runner never calls `execute_command(WaitCommand(...))`,
    and rejects queued WaitCommands (see `submit_command`), since
    the tick loop already advances `state.clock_ms` from measured
    wall-clock deltas via `GameEngine.advance_clock`. Both mechanisms
    can coexist across runners without double-advancing the same clock.
    """

    def __init__(
        self,
        engine: GameEngine,
        tick_rate_hz: float = DEFAULT_TICK_RATE_HZ,
        time_fn: Callable[[], float] = time.monotonic,
        on_tick: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        if tick_rate_hz <= 0:
            raise ValueError("tick_rate_hz must be positive")
        self._engine = engine
        self._tick_interval = 1.0 / tick_rate_hz
        self._time_fn = time_fn
        self._on_tick = on_tick

        self._queue: "asyncio.Queue[QueuedCommand]" = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._last_tick_time: Optional[float] = None
        self._stopped = asyncio.Event()

    @property
    def tick_interval(self) -> float:
        return self._tick_interval

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def submit_command(self, command: GameCommand) -> "asyncio.Future":
        """Queue *command* (e.g. ``ClickCommand(Position(1, 2))``) for the next tick.

        Safe to call from any coroutine running on the same event loop
        (e.g. a websocket message handler). Returns a Future that resolves
        (with no value) once the command has actually been applied.

        WaitCommand is rejected: time advancement here is owned exclusively by
        the tick loop's wall-clock measurement, so a queued wait would advance
        the same clock twice.
        """
        if isinstance(command, WaitCommand):
            raise ValueError(
                "AsyncGameRunner owns time advancement; submit clicks only, "
                "not WaitCommand"
            )
        loop = asyncio.get_event_loop()
        fut: "asyncio.Future" = loop.create_future()
        self._queue.put_nowait(QueuedCommand(command=command, future=fut))
        return fut

    async def start(self) -> None:
        """Start the background tick task. No-op if already running."""
        if self.running:
            return
        self._stopped.clear()
        self._last_tick_time = self._time_fn()
        self._task = asyncio.ensure_future(self._run())

    async def stop(self) -> None:
        """Stop the tick task and wait for it to finish."""
        if self._task is None:
            return
        self._stopped.set()
        try:
            await self._task
        finally:
            self._task = None

    async def _run(self) -> None:
        try:
            while not self._stopped.is_set():
                await asyncio.sleep(self._tick_interval)
                await self._tick()
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        """Advance the clock by elapsed wall-clock time and resolve once.

        Draining queued commands first (before advancing the clock) means a
        click submitted mid-tick is applied against the *previous* tick's
        resolved state, then the clock advance immediately resolves any
        motion that click just started — matching how a synchronous
        click-then-wait sequence behaves in the text-test path.
        """
        now = self._time_fn()
        last = self._last_tick_time if self._last_tick_time is not None else now
        elapsed_ms = max(0, int((now - last) * MS_PER_SECOND))
        self._last_tick_time = now

        self._drain_commands()

        if elapsed_ms > 0:
            self._engine.advance_clock(elapsed_ms)

        if self._on_tick is not None:
            await self._on_tick()

    def _drain_commands(self) -> None:
        pending: List[QueuedCommand] = []
        while True:
            try:
                pending.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        for cmd in pending:
            try:
                self._engine.execute_command(cmd.command)
            finally:
                if cmd.future is not None and not cmd.future.done():
                    cmd.future.set_result(None)
