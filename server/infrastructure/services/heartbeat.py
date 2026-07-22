"""Heartbeat monitor — detects dropped connections using periodic ping/pong messages.

Owns: ping dispatch, pong tracking, and failure declaration.
Must not own: disconnection timer handling, game room state, or ELO calculation.
"""

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

_LOGGER = logging.getLogger(__name__)

DEFAULT_PING_INTERVAL_SECONDS = 5.0
DEFAULT_PONG_TIMEOUT_SECONDS = 3.0

# Private mirror of the wire vocabulary (application/dtos): infrastructure may
# not import application, so the ping frame's shape is restated here.
_FIELD_TYPE = "type"
_MSG_PING = "ping"

# Duck-typed session attributes probed with getattr.
_ATTR_SEND = "send"
_ATTR_USERNAME = "username"
_UNKNOWN_USERNAME = "unknown"


class HeartbeatMonitor:
    """Monitors active WebSocket sessions via periodic ping/pong."""

    def __init__(
        self,
        ping_interval: float = DEFAULT_PING_INTERVAL_SECONDS,
        pong_timeout: float = DEFAULT_PONG_TIMEOUT_SECONDS,
        on_disconnect: Optional[Callable[[Any], None]] = None,
        time_fn=time.monotonic,
    ) -> None:
        self._ping_interval = ping_interval
        self._pong_timeout = pong_timeout
        self._on_disconnect = on_disconnect
        self._time_fn = time_fn
        self._last_pong: Dict[Any, float] = {}
        self._task: Optional[asyncio.Task] = None

    def register_session(self, session: Any) -> None:
        self._last_pong[session] = self._time_fn()

    def unregister_session(self, session: Any) -> None:
        self._last_pong.pop(session, None)

    def record_pong(self, session: Any) -> None:
        self._last_pong[session] = self._time_fn()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)
            await self._ping_all()

    async def _ping_all(self) -> None:
        now = self._time_fn()
        for session in list(self._last_pong.keys()):
            last = self._last_pong.get(session, 0.0)
            if (now - last) > (self._ping_interval + self._pong_timeout):
                _LOGGER.warning(
                    "Heartbeat timeout for session %s",
                    getattr(session, _ATTR_USERNAME, _UNKNOWN_USERNAME),
                )
                if self._on_disconnect:
                    self._on_disconnect(session)
                self.unregister_session(session)
            else:
                try:
                    if hasattr(session, _ATTR_SEND):
                        await session.send({_FIELD_TYPE: _MSG_PING})
                except Exception:
                    pass
