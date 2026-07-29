"""Matchmaking queue storage — the port the waiting list is kept behind.

Layer: domain (server/domain/matchmaking)
Owns: the shape of a waiting-player ticket, the contract any queue store must
satisfy, and the in-process store used when no shared one is configured.
Must not own: pairing policy (MatchmakingQueue owns the ELO bound and the
timeout), live session objects, or any driver.

The port exists because pairing is a fleet-wide decision while the queue it
reads is process-local today. Two replicas each holding their own Python list
are two disjoint player populations that can never be matched against each
other, so the list has to move somewhere both can see — without the pairing
rules following it out of the domain.

Two properties every implementation must preserve, because MatchmakingQueue
documents them and the rest of the server relies on them:

* **A pair is popped whole or not at all.** No caller may ever observe one half
  of a match removed, or re-match a player already promised to someone else.
  In-process this is the absence of an `await` between decision and eviction;
  in Redis it is a single Lua script.
* **Ticket identity is the username, not the session object.** A player has one
  queue slot fleet-wide, and the replica that pops a pair is not necessarily
  the one holding either socket.
"""

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Protocol, Tuple

_PLAYERS_PER_MATCH = 2


@dataclass(frozen=True)
class QueueTicket:
    """One waiting player, reduced to what pairing actually needs.

    Deliberately not a session: a ticket has to survive being written to a
    store another process reads, and a socket cannot.
    """

    username: str
    elo: int
    joined_at: float
    replica: str = ""
    # Carried so the replica that pops the pair can build a seat for a player
    # whose socket it does not hold. Defaults to 0 because a caller that never
    # crosses a replica boundary — a text-driven test, a single process — has no
    # use for it and should not be made to supply one.
    user_id: int = 0

    def waited(self, now: float) -> float:
        return now - self.joined_at


class QueueBackend(Protocol):
    """Where waiting players are kept between joining and being paired."""

    def now(self) -> float:
        """The clock timeouts are measured against.

        Owned by the store rather than the queue because a shared store must
        measure every replica's tickets on one clock — a per-process monotonic
        clock has no meaning to the replica that reads the ticket next.
        """

    def cached_size(self) -> int:
        """Queue depth as of the last operation, for the metrics gauge.

        Deliberately not a round trip: it is read by a synchronous gauge on
        every scrape, and a scrape must never be able to block on the store.
        """

    async def add(self, ticket: QueueTicket) -> bool:
        """Enqueue *ticket*; False if that username was already waiting."""

    async def remove(self, username: str) -> bool:
        """Remove *username*, reporting whether it was actually queued."""

    async def pop_pair(self, max_elo_diff: int) -> Optional[Tuple[QueueTicket, QueueTicket]]:
        """Atomically remove and return the longest-waiting compatible pair."""

    async def pop_expired(self, timeout_seconds: float) -> List[QueueTicket]:
        """Atomically remove and return everyone who waited past *timeout_seconds*."""


class InProcessQueueBackend:
    """The original list-in-RAM queue, kept as the no-infrastructure default.

    Preserves the synchronous-eviction property literally: `pop_pair` contains
    no `await`, so no coroutine can interleave between identifying a pair and
    evicting it.
    """

    def __init__(self, time_fn) -> None:
        self._time_fn = time_fn
        self._tickets: List[QueueTicket] = []

    def now(self) -> float:
        return self._time_fn()

    def cached_size(self) -> int:
        return len(self._tickets)

    async def add(self, ticket: QueueTicket) -> bool:
        if any(existing.username == ticket.username for existing in self._tickets):
            return False
        self._tickets.append(ticket)
        return True

    async def remove(self, username: str) -> bool:
        remaining = [t for t in self._tickets if t.username != username]
        was_queued = len(remaining) != len(self._tickets)
        self._tickets = remaining
        return was_queued

    async def pop_pair(self, max_elo_diff: int) -> Optional[Tuple[QueueTicket, QueueTicket]]:
        if len(self._tickets) < _PLAYERS_PER_MATCH:
            return None

        # Tickets are only ever appended, so scanning in place pairs the two
        # longest-waiting compatible players.
        for i, first in enumerate(self._tickets):
            for second in self._tickets[i + 1:]:
                if abs(first.elo - second.elo) <= max_elo_diff:
                    self._tickets.remove(first)
                    self._tickets.remove(second)
                    return (first, second)
        return None

    async def pop_expired(self, timeout_seconds: float) -> List[QueueTicket]:
        now = self.now()
        expired = [t for t in self._tickets if t.waited(now) >= timeout_seconds]
        if expired:
            usernames = {t.username for t in expired}
            self._tickets = [t for t in self._tickets if t.username not in usernames]
        return expired


class LocalSeatRegistry:
    """Resolves a popped ticket back to the live session it belongs to.

    Split out from the queue because in a fleet the replica that pops a pair is
    not necessarily the one holding either socket: this resolver answers for the
    sockets *this* process holds, and a broker-backed one answers for the rest.
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, object] = {}

    def bind(self, username: str, session: object) -> None:
        self._sessions[username] = session

    def release(self, username: str) -> None:
        self._sessions.pop(username, None)

    def resolve(self, ticket: QueueTicket) -> Optional[object]:
        return self._sessions.get(ticket.username)


def requeue_preserving_wait(ticket: QueueTicket, replica: str) -> QueueTicket:
    """The same ticket re-addressed to *replica*, with its original join time.

    Used when a popped ticket cannot be resolved to a session: the player goes
    back to waiting, and preserving `joined_at` means their queue timeout still
    fires when it was always going to, rather than being reset by an accident of
    infrastructure.
    """
    return replace(ticket, replica=replica)
