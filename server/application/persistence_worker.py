"""Persistence worker — writes finished games off the game's critical path.

Layer: application (server/application)
Owns: consuming the durable `game.finished` stream, batching the writes, and
reporting how deep the backlog is so an autoscaler can act on it.
Must not own: the SQL (the database adapter), which games are eligible
(`GamePersistenceService`), or the broker.

**Why the write moved here at all.** `GameRoom._settle_elo_for_game_end` used to
await the database inside the room's own shutdown path. At 83,000 finished games
per second that is untenable, and worse, a slow database would stall rooms over
an operation with nothing to do with the game. Now the authority publishes to the
durable stream and frees the room; a database slowdown queues messages in the
broker instead of pushing back into the simulation.

**Batching is what makes the write rate affordable**, but a batch must never sit
in memory waiting to fill: a game that finished is a rating a player is waiting
to see. So a batch is flushed when it is full *or* when it is old, whichever
comes first, and the age bound is what makes the latency predictable rather than
dependent on how busy the fleet happens to be.

**Delivery is at-least-once, deliberately.** The alternative — acknowledging
before writing — turns every worker crash into a silently lost game. Redelivery
is safe because `ON CONFLICT (room_id) DO NOTHING` makes a repeated save resolve
to the row already there; that is why Step 3's idempotency was a prerequisite for
this step rather than a refinement of it.
"""

import asyncio
import logging
import time
from typing import Any, List, Optional

from server.application.game_result import GameResult
from server.application.game_result_codec import decode_game_result
from server.domain.coordination.broker import SUBJECT_GAME_FINISHED

_LOGGER = logging.getLogger(__name__)

# The named consumer group every worker joins. One group means the stream is a
# work queue shared between them rather than a broadcast each writes in full.
DEFAULT_CONSUMER_GROUP = "kfchess_persistence"

# Rows per transaction. Large enough to amortise the round trip, small enough
# that one failure re-does a bounded amount of work on redelivery.
DEFAULT_BATCH_SIZE = 50

# How long a partially-filled batch may wait. A finished game is a rating a
# player is watching for, so this bounds that wait regardless of load.
DEFAULT_BATCH_LINGER_SECONDS = 1.0


class PersistenceWorker:
    """Drains the finished-game stream into the database, in batches."""

    def __init__(
        self,
        broker: Any,
        persistence_service: Any,
        consumer_group: str = DEFAULT_CONSUMER_GROUP,
        batch_size: int = DEFAULT_BATCH_SIZE,
        linger_seconds: float = DEFAULT_BATCH_LINGER_SECONDS,
        clock_fn=time.monotonic,
    ) -> None:
        self._broker = broker
        self._persistence_service = persistence_service
        self._consumer_group = consumer_group
        self._batch_size = batch_size
        self._linger_seconds = linger_seconds
        self._clock_fn = clock_fn
        self._pending: List[GameResult] = []
        self._oldest_pending_at: Optional[float] = None
        self._subscription: Optional[Any] = None
        self._linger_task: Optional[asyncio.Task] = None
        # Serializes flushes so a linger-triggered one and a full-batch one
        # cannot both write the same games.
        self._flush_lock = asyncio.Lock()
        self._written = 0

    @property
    def pending_count(self) -> int:
        """Games accepted but not yet written — the worker's own backlog.

        Exported so an autoscaler can see the queue growing *inside* a worker,
        not only in the broker. A pool that is keeping up shows this near zero
        even under load; a stalled database shows it pinned at the batch size.
        """
        return len(self._pending)

    @property
    def written_count(self) -> int:
        return self._written

    async def start(self) -> None:
        """Join the consumer group and begin draining."""
        if self._subscription is not None:
            return
        self._subscription = await self._broker.subscribe_durable(
            SUBJECT_GAME_FINISHED, self._on_message, queue_group=self._consumer_group
        )
        self._linger_task = asyncio.ensure_future(self._linger_loop())
        _LOGGER.info("Persistence worker joined group %s", self._consumer_group)

    async def stop(self) -> None:
        """Stop consuming and write whatever is already accepted.

        The final flush matters: those games were acknowledged to the broker, so
        nothing will redeliver them. Exiting without writing them is the one way
        this design can genuinely lose a game.
        """
        if self._linger_task is not None:
            self._linger_task.cancel()
            try:
                await self._linger_task
            except asyncio.CancelledError:
                pass
            self._linger_task = None

        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None

        await self.flush()
        _LOGGER.info("Persistence worker stopped after writing %d games", self._written)

    async def _on_message(self, payload: dict) -> None:
        """Accept one finished game, flushing when the batch fills.

        A message that cannot be decoded is dropped rather than raised on: the
        broker's handler acknowledges on a clean return, and a message no
        consumer can ever read would otherwise redeliver forever at the head of
        the queue, stalling every game behind it.
        """
        result = decode_game_result(payload)
        if result is None:
            return

        self._pending.append(result)
        if self._oldest_pending_at is None:
            self._oldest_pending_at = self._clock_fn()

        if len(self._pending) >= self._batch_size:
            await self.flush()

    async def _linger_loop(self) -> None:
        """Flush a partially-filled batch once it has waited long enough."""
        try:
            while True:
                await asyncio.sleep(self._linger_seconds / 2)
                if self._batch_is_stale():
                    await self.flush()
        except asyncio.CancelledError:
            pass

    def _batch_is_stale(self) -> bool:
        if not self._pending or self._oldest_pending_at is None:
            return False
        return (self._clock_fn() - self._oldest_pending_at) >= self._linger_seconds

    async def flush(self) -> int:
        """Write every accepted game, reporting how many were handled.

        Each game is written independently rather than as one transaction: they
        share nothing, and a single bad game must not roll back the forty-nine
        good ones batched with it. The batching is about amortising the round
        trip, not about atomicity across unrelated games.
        """
        async with self._flush_lock:
            batch, self._pending = self._pending, []
            self._oldest_pending_at = None

            for result in batch:
                await self._write(result)
            return len(batch)

    async def _write(self, result: GameResult) -> None:
        """Persist one game, keeping a failure from taking the batch with it.

        A failed write is logged and dropped rather than retried here. The
        message it came from was already acknowledged, so a retry loop in this
        process would be the only thing standing between a transient database
        error and a lost game — which is a job for the stream's redelivery, not
        for an in-memory loop that dies with the worker.
        """
        try:
            game_id = await self._persistence_service.persist_game(result)
        except Exception as exc:
            _LOGGER.exception("Writing room %s failed: %s", result.room_id, exc)
            return

        if game_id is None:
            _LOGGER.warning("Room %s could not be persisted", result.room_id)
            return
        self._written += 1
