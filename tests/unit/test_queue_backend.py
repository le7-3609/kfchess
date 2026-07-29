"""Unit tests for the queue store port and its in-process implementation.

The point of the port is that MatchmakingQueue's behaviour is identical whichever
store is behind it, so these pin the contract every implementation must satisfy —
and `tests/infra/test_redis_matchmaking.py` runs the same expectations against
the Redis one.
"""

import pytest

from server.domain.matchmaking.queue import MatchmakingQueue
from server.domain.matchmaking.queue_backend import (
    InProcessQueueBackend,
    LocalSeatRegistry,
    QueueTicket,
    requeue_preserving_wait,
)


class MockSession:
    def __init__(self, username: str, elo: int):
        self.username = username
        self.elo = elo


def _clock(start: float = 100.0):
    holder = [start]

    def read() -> float:
        return holder[0]

    return holder, read


@pytest.mark.asyncio
async def test_a_ticket_is_added_once_per_username():
    backend = InProcessQueueBackend(lambda: 0.0)
    ticket = QueueTicket(username="Alice", elo=1200, joined_at=0.0)

    assert await backend.add(ticket) is True
    assert await backend.add(ticket) is False
    assert backend.cached_size() == 1


@pytest.mark.asyncio
async def test_removing_reports_whether_the_player_was_there():
    backend = InProcessQueueBackend(lambda: 0.0)
    await backend.add(QueueTicket(username="Alice", elo=1200, joined_at=0.0))

    assert await backend.remove("Alice") is True
    assert await backend.remove("Alice") is False


@pytest.mark.asyncio
async def test_pop_pair_takes_both_or_neither():
    backend = InProcessQueueBackend(lambda: 0.0)
    await backend.add(QueueTicket(username="Alice", elo=1200, joined_at=0.0))
    await backend.add(QueueTicket(username="Bob", elo=1400, joined_at=1.0))

    assert await backend.pop_pair(max_elo_diff=100) is None
    assert backend.cached_size() == 2, "an unmatched scan must evict nobody"

    pair = await backend.pop_pair(max_elo_diff=300)
    assert {t.username for t in pair} == {"Alice", "Bob"}
    assert backend.cached_size() == 0


@pytest.mark.asyncio
async def test_pop_expired_takes_only_those_past_the_timeout():
    holder, read = _clock()
    backend = InProcessQueueBackend(read)
    await backend.add(QueueTicket(username="Early", elo=1200, joined_at=100.0))
    holder[0] = 130.0
    await backend.add(QueueTicket(username="Late", elo=1200, joined_at=130.0))

    holder[0] = 170.0
    expired = await backend.pop_expired(timeout_seconds=60.0)

    assert [t.username for t in expired] == ["Early"]
    assert backend.cached_size() == 1


def test_requeueing_preserves_the_original_wait():
    """A player returned to the queue keeps their place in it.

    Resetting `joined_at` would push their bot-fallback timeout back by however
    long the infrastructure took to fail, which is a delay they did nothing to
    earn.
    """
    ticket = QueueTicket(username="Alice", elo=1200, joined_at=17.0, replica="a")

    restored = requeue_preserving_wait(ticket, "b")

    assert restored.joined_at == 17.0
    assert restored.replica == "b"


def test_the_seat_registry_resolves_only_what_it_holds():
    registry = LocalSeatRegistry()
    alice = MockSession("Alice", 1200)
    registry.bind("Alice", alice)

    assert registry.resolve(QueueTicket("Alice", 1200, 0.0)) is alice
    assert registry.resolve(QueueTicket("Bob", 1200, 0.0)) is None

    registry.release("Alice")
    assert registry.resolve(QueueTicket("Alice", 1200, 0.0)) is None


@pytest.mark.asyncio
async def test_the_queue_uses_an_injected_backend():
    """The swap is the whole point: same queue, same calls, different store."""
    backend = InProcessQueueBackend(lambda: 0.0)
    mm = MatchmakingQueue(backend=backend, replica_id="replica-a")

    await mm.join_queue(MockSession("Alice", 1200))

    assert backend.cached_size() == 1
    assert mm.queue_length == 1


@pytest.mark.asyncio
async def test_a_ticket_records_the_replica_that_queued_it():
    backend = InProcessQueueBackend(lambda: 0.0)
    mm = MatchmakingQueue(backend=backend, replica_id="replica-a")

    await mm.join_queue(MockSession("Alice", 1200))
    pair_source = await backend.pop_expired(timeout_seconds=-1.0)

    assert pair_source[0].replica == "replica-a"


@pytest.mark.asyncio
async def test_an_unresolvable_ticket_is_returned_to_the_queue():
    """Both halves go back, not just the one this replica could seat."""
    backend = InProcessQueueBackend(lambda: 0.0)
    mm = MatchmakingQueue(backend=backend, replica_id="replica-a", max_elo_diff=100)
    await mm.join_queue(MockSession("Alice", 1200))
    # Bob is queued directly into the store, standing in for a player whose
    # socket is held by another replica: no local session, no resolver.
    await backend.add(QueueTicket(username="Bob", elo=1210, joined_at=0.0, replica="replica-b"))

    assert await mm.try_match() is None
    assert backend.cached_size() == 2


@pytest.mark.asyncio
async def test_a_remote_resolver_makes_an_otherwise_unseatable_pair_match():
    """What Step 6's broker supplies: a way to reach a seat held elsewhere."""
    backend = InProcessQueueBackend(lambda: 0.0)
    remote = MockSession("Bob", 1210)
    mm = MatchmakingQueue(
        backend=backend,
        replica_id="replica-a",
        max_elo_diff=100,
        remote_resolver=lambda ticket: remote if ticket.username == "Bob" else None,
    )
    await mm.join_queue(MockSession("Alice", 1200))
    await backend.add(QueueTicket(username="Bob", elo=1210, joined_at=0.0, replica="replica-b"))

    pair = await mm.try_match()

    assert pair is not None
    assert {session.username for session in pair} == {"Alice", "Bob"}


@pytest.mark.asyncio
async def test_a_timed_out_ticket_this_replica_cannot_reach_is_left_to_its_owner():
    """Every replica sweeps the same store, so each rescues only its own players.

    Returning a session-less ticket would make the caller hand a bot to a player
    it has no socket for.
    """
    backend = InProcessQueueBackend(lambda: 0.0)
    mm = MatchmakingQueue(backend=backend, replica_id="replica-a", timeout_seconds=1.0)
    await backend.add(QueueTicket(username="Bob", elo=1200, joined_at=-100.0, replica="replica-b"))

    assert await mm.check_timeouts() == []
