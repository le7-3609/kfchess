"""Matchmaking queue — pairs waiting players by ELO rating.

Layer: domain (server/domain/matchmaking)
Owns: ELO-bound pairing (+/-100 rating points by default), the queue wait
timeout (60s by default), and the resolution of a popped ticket back to the
session that must be seated.
Must not own: where the waiting list is stored (QueueBackend), game room
creation, network transport, or database persistence.

The pairing invariant, unchanged since the in-process version: a pair is
identified and evicted as one indivisible act, so no coroutine and no other
replica can observe or re-match a half-evicted pair. In-process that is the
absence of an `await` between decision and eviction; against Redis it is a
single Lua script. `try_match` never has to know which.
"""

import asyncio
import logging
import time
from typing import Any, Callable, List, Optional, Tuple

from server.domain.matchmaking.queue_backend import (
    InProcessQueueBackend,
    LocalSeatRegistry,
    QueueBackend,
    QueueTicket,
    requeue_preserving_wait,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_ELO_DIFF = 100
DEFAULT_QUEUE_TIMEOUT_SECONDS = 60.0

# Identifies which replica queued a player, so a pair popped here can be traced
# back to the sockets it has to be delivered to. Empty for a single process,
# where there is only ever one answer.
DEFAULT_REPLICA_ID = ""

SeatResolver = Callable[[QueueTicket], Optional[Any]]


class MatchmakingQueue:
    """ELO-bounded pairing over a queue store that may be shared or local."""

    def __init__(
        self,
        max_elo_diff: int = DEFAULT_MAX_ELO_DIFF,
        timeout_seconds: float = DEFAULT_QUEUE_TIMEOUT_SECONDS,
        time_fn=time.monotonic,
        backend: Optional[QueueBackend] = None,
        replica_id: str = DEFAULT_REPLICA_ID,
        remote_resolver: Optional[SeatResolver] = None,
    ) -> None:
        if max_elo_diff < 0:
            raise ValueError("max_elo_diff must not be negative")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        self._max_elo_diff = max_elo_diff
        self._timeout_seconds = timeout_seconds
        self._replica_id = replica_id
        self._backend = backend or InProcessQueueBackend(time_fn)
        self._local_seats = LocalSeatRegistry()
        # Answers for sessions held by *other* replicas. None until a broker
        # exists to reach them, which is why an unresolvable ticket is returned
        # to the queue rather than dropped.
        self._remote_resolver = remote_resolver
        # Serializes this replica's own calls. The store's atomicity is what
        # protects against other replicas; this protects against this one's own
        # coroutines racing between the pop and the seat lookup that follows it.
        self._lock = asyncio.Lock()

    @property
    def queue_length(self) -> int:
        """Players waiting, as of the last store operation.

        Read synchronously by the metrics gauge on every scrape, so it must not
        become a network round trip when the store is remote.
        """
        return self._backend.cached_size()

    async def join_queue(self, session: Any) -> None:
        """Add player session to queue if not already present."""
        async with self._lock:
            ticket = QueueTicket(
                username=session.username,
                elo=session.elo,
                joined_at=self._backend.now(),
                replica=self._replica_id,
                # Read defensively: a seat contract is only obliged to carry a
                # username and a rating, and the bot adapter and the text-driven
                # test sessions genuinely have no user id.
                user_id=getattr(session, "user_id", 0) or 0,
            )
            if not await self._backend.add(ticket):
                return
            self._local_seats.bind(session.username, session)

        _LOGGER.info(
            "Player %s (ELO=%d) joined matchmaking queue", session.username, session.elo
        )

    async def leave_queue(self, session: Any) -> bool:
        """Remove player session from queue, reporting whether it was queued.

        Idempotent: the disconnect path calls this for every closing socket,
        including players who never queued, so a no-op must stay silent.
        """
        async with self._lock:
            was_queued = await self._backend.remove(session.username)
            self._local_seats.release(session.username)

        if was_queued:
            _LOGGER.info("Player %s left matchmaking queue", session.username)
        return was_queued

    async def try_match(self) -> Optional[Tuple[Any, Any]]:
        """Attempt to find and pair two compatible players.

        The store pops both tickets or neither. Whatever it hands back is then
        resolved to two live sessions; a ticket that cannot be resolved goes
        back into the queue with its original join time, so the player keeps
        waiting on their original timeout rather than being lost to an
        infrastructure gap.
        """
        async with self._lock:
            pair = await self._backend.pop_pair(self._max_elo_diff)
            if pair is None:
                return None
            return await self._seat_pair(pair)

    async def _seat_pair(self, pair: Tuple[QueueTicket, QueueTicket]) -> Optional[Tuple[Any, Any]]:
        first, second = pair
        sessions = [self._resolve(ticket) for ticket in pair]
        if any(session is None for session in sessions):
            await self._return_unseatable(pair, sessions)
            return None

        self._local_seats.release(first.username)
        self._local_seats.release(second.username)
        _LOGGER.info(
            "Matched players %s (%d) & %s (%d) [diff=%d]",
            first.username, first.elo, second.username, second.elo,
            abs(first.elo - second.elo),
        )
        return (sessions[0], sessions[1])

    def _resolve(self, ticket: QueueTicket) -> Optional[Any]:
        """Find the session a ticket names, locally first and then remotely."""
        session = self._local_seats.resolve(ticket)
        if session is not None:
            return session
        return self._remote_resolver(ticket) if self._remote_resolver is not None else None

    async def _return_unseatable(
        self, pair: Tuple[QueueTicket, QueueTicket], sessions: List[Optional[Any]]
    ) -> None:
        """Put a popped-but-unreachable pair back, keeping their wait intact.

        Reached when a ticket names a socket this replica does not hold and no
        resolver can reach it. Both halves go back — returning only the
        resolvable one would leave the other player silently evicted from a
        queue they never left.
        """
        for ticket, session in zip(pair, sessions):
            if session is None:
                _LOGGER.warning(
                    "Popped ticket for %s names replica %r, which this process "
                    "cannot reach; returning both players to the queue",
                    ticket.username, ticket.replica or "<local>",
                )
            await self._backend.add(requeue_preserving_wait(ticket, ticket.replica))

    async def check_timeouts(self) -> List[Any]:
        """Evict players who have been in queue longer than timeout_seconds.

        Only players this replica can actually reach are returned: a timed-out
        ticket belonging to another replica is that replica's to rescue, and it
        sweeps the same store on the same schedule.
        """
        async with self._lock:
            expired = await self._backend.pop_expired(self._timeout_seconds)
            sessions = []
            for ticket in expired:
                session = self._resolve(ticket)
                self._local_seats.release(ticket.username)
                if session is None:
                    continue
                _LOGGER.info("Matchmaking timeout for player %s", ticket.username)
                sessions.append(session)
            return sessions
