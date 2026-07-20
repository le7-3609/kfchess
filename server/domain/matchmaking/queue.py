"""Matchmaking queue — pairs waiting players by ELO rating.

Layer: domain (server/domain/matchmaking)
Owns: the waiting-player queue, ELO-bound pairing (+/-100 rating points by
default), and queue wait timeout (60s by default).
Must not own: game room creation, network transport, or database persistence.

Optimization B: Synchronous eviction pattern. When try_match() identifies a
matching pair, they are evicted from the queue synchronously (zero await
statements between match identification and list pop), avoiding coroutine
race conditions at await boundaries.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_ELO_DIFF = 100
DEFAULT_QUEUE_TIMEOUT_SECONDS = 60.0


@dataclass
class QueueEntry:
    session: Any
    joined_at: float

    @property
    def username(self) -> str:
        return self.session.username

    @property
    def elo(self) -> int:
        return self.session.elo


class MatchmakingQueue:
    """Thread/coroutine-safe matchmaking queue engine."""

    def __init__(
        self,
        max_elo_diff: int = DEFAULT_MAX_ELO_DIFF,
        timeout_seconds: float = DEFAULT_QUEUE_TIMEOUT_SECONDS,
        time_fn=time.monotonic,
    ) -> None:
        if max_elo_diff < 0:
            raise ValueError("max_elo_diff must not be negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._max_elo_diff = max_elo_diff
        self._timeout_seconds = timeout_seconds
        self._time_fn = time_fn
        self._queue: List[QueueEntry] = []
        self._lock = asyncio.Lock()

    @property
    def queue_length(self) -> int:
        return len(self._queue)

    async def join_queue(self, session: Any) -> None:
        """Add player session to queue if not already present."""
        async with self._lock:
            if any(entry.session is session for entry in self._queue):
                return
            entry = QueueEntry(session=session, joined_at=self._time_fn())
            self._queue.append(entry)
            _LOGGER.info("Player %s (ELO=%d) joined matchmaking queue", session.username, session.elo)

    async def leave_queue(self, session: Any) -> bool:
        """Remove player session from queue, reporting whether it was queued.

        Idempotent: the disconnect path calls this for every closing socket,
        including players who never queued, so a no-op must stay silent.
        """
        async with self._lock:
            remaining = [e for e in self._queue if e.session is not session]
            was_queued = len(remaining) != len(self._queue)
            self._queue = remaining

        if was_queued:
            _LOGGER.info("Player %s left matchmaking queue", session.username)
        return was_queued

    async def try_match(self) -> Optional[Tuple[Any, Any]]:
        """Attempt to find and pair two compatible players.

        Optimization B: Atomic scan and synchronous eviction.
        Identifies a pair and pops them from the array immediately before returning,
        preventing double-matching during coroutine context switches.
        """
        async with self._lock:
            if len(self._queue) < 2:
                return None

            # Already in join order: entries are only ever appended, so scanning
            # the list as-is pairs the two longest-waiting compatible players.
            entries = list(self._queue)

            for i in range(len(entries)):
                e1 = entries[i]
                for j in range(i + 1, len(entries)):
                    e2 = entries[j]
                    elo_diff = abs(e1.elo - e2.elo)

                    if elo_diff <= self._max_elo_diff:
                        # SYNCHRONOUS EVICTION — zero await calls between match & pop!
                        self._queue.remove(e1)
                        self._queue.remove(e2)
                        _LOGGER.info(
                            "Matched players %s (%d) & %s (%d) [diff=%d]",
                            e1.username, e1.elo, e2.username, e2.elo, elo_diff
                        )
                        return (e1.session, e2.session)

            return None

    async def check_timeouts(self) -> List[Any]:
        """Evict players who have been in queue longer than timeout_seconds."""
        async with self._lock:
            now = self._time_fn()
            timed_out: List[Any] = []
            remaining: List[QueueEntry] = []

            for entry in self._queue:
                if (now - entry.joined_at) >= self._timeout_seconds:
                    timed_out.append(entry.session)
                    _LOGGER.info("Matchmaking timeout for player %s", entry.username)
                else:
                    remaining.append(entry)

            self._queue = remaining
            return timed_out
