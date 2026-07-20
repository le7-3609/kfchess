"""Background timing services.

Owns: the asyncio tasks that run on their own clocks — ping/pong heartbeats and
the bot move cadence.
Must not own: domain invariants, use-case policy, or wire-frame encoding. The
disconnect countdown lives in server/application/disconnect_handler.py instead:
it decides when a seat forfeits, which is policy rather than mechanism.
"""

from server.infrastructure.services.bot_driver import (
    DEFAULT_BOT_MOVE_INTERVAL_SECONDS,
    BotDriver,
)
from server.infrastructure.services.heartbeat import (
    DEFAULT_PING_INTERVAL_SECONDS,
    DEFAULT_PONG_TIMEOUT_SECONDS,
    HeartbeatMonitor,
)

__all__ = [
    "BotDriver",
    "DEFAULT_BOT_MOVE_INTERVAL_SECONDS",
    "HeartbeatMonitor",
    "DEFAULT_PING_INTERVAL_SECONDS",
    "DEFAULT_PONG_TIMEOUT_SECONDS",
]
