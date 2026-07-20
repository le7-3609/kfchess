"""Unit tests for shared.runtime.async_runner.AsyncGameRunner."""

import asyncio
import unittest

from shared.bootstrap import build_realtime_service
from shared.config.game_config import GameConfig
from shared.engine.input_commands import ClickCommand, WaitCommand
from shared.model.position import Position
from shared.runtime.async_runner import AsyncGameRunner


BOARD = [
    "Board:",
    "wR . . .",
    ". . . .",
    "wK . . .",
    ". . . .",
    ". . . .",
    ". . . .",
    ". . . .",
    ". . . bK",
]


def _build_runner(tick_rate_hz: float = 20.0, ms_per_square: int = 1000):
    config = GameConfig()
    service = build_realtime_service(config=config, ms_per_square=ms_per_square)
    service.execute(BOARD + ["Commands:"])

    clock = {"t": 0.0}

    def fake_time():
        return clock["t"]

    runner = AsyncGameRunner(service._engine, tick_rate_hz=tick_rate_hz, time_fn=fake_time)
    return service, runner, clock


class TestAsyncGameRunner(unittest.TestCase):
    def test_tick_advances_clock_and_resolves_motion(self) -> None:
        async def scenario():
            service, runner, clock = _build_runner(ms_per_square=1000)
            await runner.start()

            select_fut = runner.submit_command(ClickCommand(50, 50))   # select wR at (0,0)
            move_fut = runner.submit_command(ClickCommand(350, 50))    # move to (0,3): 3 squares away
            # Both commands are queued concurrently; only the tick loop drains them.
            self.assertFalse(select_fut.done())
            self.assertFalse(move_fut.done())

            # Advance wall clock across several ticks, well past 3000ms travel time.
            for _ in range(70):
                clock["t"] += runner.tick_interval
                await asyncio.sleep(0)
                await runner._tick()

            await runner.stop()

            self.assertTrue(select_fut.done())
            self.assertTrue(move_fut.done())

            board = service._board_repo.get_board()
            self.assertIsNone(board.get_piece(Position(0, 0)))
            moved_piece = board.get_piece(Position(0, 3))
            self.assertIsNotNone(moved_piece)
            self.assertEqual(moved_piece.piece_type, "R")

        asyncio.run(scenario())

    def test_rejects_queued_wait_command(self) -> None:
        async def scenario():
            service, runner, _clock = _build_runner()
            with self.assertRaises(ValueError):
                runner.submit_command(WaitCommand(100))

        asyncio.run(scenario())

    def test_start_is_idempotent_and_stop_halts_ticking(self) -> None:
        async def scenario():
            _service, runner, clock = _build_runner()
            await runner.start()
            await runner.start()  # no-op, should not raise or spawn a second task
            self.assertTrue(runner.running)

            await runner.stop()
            self.assertFalse(runner.running)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
