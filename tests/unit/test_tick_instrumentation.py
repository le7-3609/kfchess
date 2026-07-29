"""Unit tests for the tick-duration observer on AsyncGameRunner.

Tick duration is the game tier's real health signal: a 20 Hz runner has a 50 ms
budget, and time past it is simulation lag every player on the process feels —
which no CPU percentage reveals, since the loop can be behind while the machine
is mostly idle.
"""

import asyncio

import pytest

from core.runtime.async_runner import AsyncGameRunner


class _StubEngine:
    """Just enough engine for the runner to tick against."""

    def __init__(self, on_advance=None):
        self.advanced_ms = []
        self._on_advance = on_advance

    def advance_clock(self, elapsed_ms: int) -> None:
        self.advanced_ms.append(elapsed_ms)
        if self._on_advance is not None:
            self._on_advance()

    def execute_command(self, command) -> None:
        pass


@pytest.mark.asyncio
async def test_each_tick_reports_its_duration():
    durations = []
    runner = AsyncGameRunner(
        engine=_StubEngine(), tick_rate_hz=50.0, on_tick_duration=durations.append
    )

    await runner.start()
    await asyncio.sleep(0.1)
    await runner.stop()

    assert durations, "no tick was measured"
    assert all(duration >= 0 for duration in durations)


@pytest.mark.asyncio
async def test_the_measurement_includes_the_broadcast_callback():
    """The broadcast is part of the tick's cost; measuring only the simulation
    would report a healthy tick while clients waited on a slow write."""
    durations = []

    async def slow_broadcast():
        await asyncio.sleep(0.05)

    runner = AsyncGameRunner(
        engine=_StubEngine(),
        tick_rate_hz=50.0,
        on_tick=slow_broadcast,
        on_tick_duration=durations.append,
    )

    await runner.start()
    await asyncio.sleep(0.2)
    await runner.stop()

    assert max(durations) >= 0.04


@pytest.mark.asyncio
async def test_a_failing_tick_is_still_measured():
    """A tick that raised is exactly the kind that ran long; dropping its sample
    would hide the overrun that caused it.

    The failure itself still propagates out of the runner — that is the existing
    contract, unchanged here — so `stop()` re-raises it; what matters is that
    the measurement was taken before it did.
    """
    durations = []
    boom_count = {"n": 0}

    def explode():
        boom_count["n"] += 1
        raise RuntimeError("tick failed")

    runner = AsyncGameRunner(
        engine=_StubEngine(on_advance=explode),
        tick_rate_hz=50.0,
        on_tick_duration=durations.append,
    )

    await runner.start()
    await asyncio.sleep(0.1)
    with pytest.raises(RuntimeError):
        await runner.stop()

    assert boom_count["n"] >= 1
    assert durations, "a failing tick was never measured"


@pytest.mark.asyncio
async def test_a_raising_observer_cannot_stop_the_simulation():
    """Measurement must never be able to break the thing it measures."""

    def broken_observer(_duration):
        raise RuntimeError("metrics backend is down")

    engine = _StubEngine()
    runner = AsyncGameRunner(
        engine=engine, tick_rate_hz=50.0, on_tick_duration=broken_observer
    )

    await runner.start()
    await asyncio.sleep(0.1)
    await runner.stop()

    assert engine.advanced_ms, "the simulation stopped advancing"


@pytest.mark.asyncio
async def test_a_runner_without_an_observer_still_ticks():
    """The hook is optional, so `core` keeps no metrics dependency."""
    engine = _StubEngine()
    runner = AsyncGameRunner(engine=engine, tick_rate_hz=50.0)

    await runner.start()
    await asyncio.sleep(0.1)
    await runner.stop()

    assert engine.advanced_ms
