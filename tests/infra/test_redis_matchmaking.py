"""MatchmakingQueue against a real Redis.

What these prove that the in-process backend cannot: that the Lua script really
is one indivisible unit across concurrent callers, and that two independently
constructed queues — standing in for two replicas — see one player population.

The interface under test is unchanged. Every call below is `join_queue`,
`leave_queue`, `try_match` or `check_timeouts`, exactly as `MatchmakingUseCase`
makes them; only the store behind them is different.
"""

import asyncio

import pytest
import pytest_asyncio

from server.domain.matchmaking.queue import MatchmakingQueue
from server.infrastructure.coordination.redis_queue_backend import RedisQueueBackend

pytestmark = pytest.mark.infra


class MockSession:
    def __init__(self, username: str, elo: int):
        self.username = username
        self.elo = elo


def _queue(connection, replica: str, **kwargs) -> MatchmakingQueue:
    return MatchmakingQueue(
        backend=RedisQueueBackend(connection), replica_id=replica, **kwargs
    )


async def _depth(connection) -> int:
    """The queue's true depth, read straight from Redis.

    `MatchmakingQueue.queue_length` deliberately answers from a cache refreshed
    by *that replica's* own calls, because it is read by a synchronous metrics
    gauge that must never block on a round trip. Assertions about what the
    fleet shares therefore have to ask Redis, not a replica's local view.
    """
    return int(await connection.client.zcard(connection.key("mm:wait")))


@pytest.mark.asyncio
async def test_join_and_leave_round_trip_through_redis(redis_connection):
    mm = _queue(redis_connection, "replica-a")
    alice = MockSession("Alice", 1200)

    await mm.join_queue(alice)
    assert mm.queue_length == 1

    await mm.join_queue(alice)
    assert mm.queue_length == 1, "a repeated join must occupy one slot, not two"

    assert await mm.leave_queue(alice) is True
    assert mm.queue_length == 0
    assert await mm.leave_queue(alice) is False


@pytest.mark.asyncio
async def test_pairs_within_the_elo_window(redis_connection):
    mm = _queue(redis_connection, "replica-a", max_elo_diff=100)
    await mm.join_queue(MockSession("Alice", 1200))
    await mm.join_queue(MockSession("Bob", 1250))

    match = await mm.try_match()

    assert match is not None
    assert {match[0].username, match[1].username} == {"Alice", "Bob"}
    assert mm.queue_length == 0


@pytest.mark.asyncio
async def test_refuses_a_pair_outside_the_elo_window(redis_connection):
    mm = _queue(redis_connection, "replica-a", max_elo_diff=100)
    await mm.join_queue(MockSession("Alice", 1200))
    await mm.join_queue(MockSession("Bob", 1350))

    assert await mm.try_match() is None
    assert mm.queue_length == 2, "both players stay queued when nobody is compatible"


@pytest.mark.asyncio
async def test_the_longest_waiting_compatible_player_is_the_anchor(redis_connection):
    """The ZRANGEBYSCORE window must not reorder the queue by rating.

    Three compatible players and one pop: the two who waited longest are the
    ones who get the game, not the two whose ratings happen to be closest.
    """
    mm = _queue(redis_connection, "replica-a", max_elo_diff=100)
    await mm.join_queue(MockSession("First", 1200))
    await asyncio.sleep(0.01)
    await mm.join_queue(MockSession("Second", 1290))
    await asyncio.sleep(0.01)
    await mm.join_queue(MockSession("Third", 1205))

    match = await mm.try_match()

    assert {match[0].username, match[1].username} == {"First", "Second"}


@pytest.mark.asyncio
async def test_a_pair_is_popped_whole_or_not_at_all_under_concurrency(redis_connection):
    """The hard requirement of Step 5, exercised rather than asserted.

    Twenty queues poll one Redis at once against a population of ten players.
    If the pop were not atomic, some player would appear in two matches — the
    exact failure that puts one human in two games — or a partial eviction would
    leave a queue entry pointing at a player already seated.
    """
    population = [MockSession(f"P{i}", 1200) for i in range(10)]
    joiner = _queue(redis_connection, "joiner")
    for session in population:
        await joiner.join_queue(session)

    contenders = [_queue(redis_connection, f"replica-{i}") for i in range(20)]
    # Every replica resolves every player, so a pop by any of them is seatable
    # and the only thing that can go wrong is the atomicity under test.
    for queue in contenders:
        for session in population:
            queue._local_seats.bind(session.username, session)

    results = await asyncio.gather(*(queue.try_match() for queue in contenders))

    matched = [pair for pair in results if pair is not None]
    seated = [session.username for pair in matched for session in pair]
    assert len(matched) == 5, f"ten players must yield exactly five games, got {len(matched)}"
    assert len(seated) == len(set(seated)), f"a player was seated twice: {seated}"
    assert await _depth(redis_connection) == 0, "a half-evicted pair left a ticket behind"


@pytest.mark.asyncio
async def test_two_replicas_share_one_player_population(redis_connection):
    """Step 5's exit criterion at the queue level.

    Alice queues on replica A and Bob on replica B. Neither queue holds the
    other's session, yet the pair forms — which is precisely what two in-process
    lists can never do.
    """
    replica_a = _queue(redis_connection, "replica-a")
    replica_b = _queue(redis_connection, "replica-b")
    await replica_a.join_queue(MockSession("Alice", 1200))
    await replica_b.join_queue(MockSession("Bob", 1210))

    assert await _depth(redis_connection) == 2, "both replicas queue into one store"

    popped = await replica_a._backend.pop_pair(100)

    assert popped is not None
    assert {t.username for t in popped} == {"Alice", "Bob"}
    assert {t.replica for t in popped} == {"replica-a", "replica-b"}


@pytest.mark.asyncio
async def test_an_unreachable_ticket_returns_both_players_to_the_queue(redis_connection):
    """Replica A pops a pair it cannot seat, because Bob's socket is on B.

    Both go back, with their original join times, so neither is silently evicted
    from a queue they never left and neither has their timeout reset.
    """
    replica_a = _queue(replica := redis_connection, "replica-a")
    replica_b = _queue(replica, "replica-b")
    await replica_a.join_queue(MockSession("Alice", 1200))
    await replica_b.join_queue(MockSession("Bob", 1210))

    assert await replica_a.try_match() is None
    assert replica_a.queue_length == 2

    restored = await replica_a._backend.pop_pair(100)
    assert {t.username for t in restored} == {"Alice", "Bob"}


@pytest.mark.asyncio
async def test_timeouts_evict_across_replicas_exactly_once(redis_connection):
    """Two replicas sweep the same store; a timed-out player is rescued once.

    Without atomic eviction both would hand the same player a bot, which is two
    rooms for one person.
    """
    replica_a = _queue(redis_connection, "replica-a", timeout_seconds=0.05)
    replica_b = _queue(redis_connection, "replica-b", timeout_seconds=0.05)
    alice = MockSession("Alice", 1200)
    await replica_a.join_queue(alice)
    replica_b._local_seats.bind("Alice", alice)

    await asyncio.sleep(0.1)
    swept = await asyncio.gather(replica_a.check_timeouts(), replica_b.check_timeouts())

    rescued = [session for batch in swept for session in batch]
    assert len(rescued) == 1
    assert rescued[0].username == "Alice"
    assert replica_a.queue_length == 0


@pytest.mark.asyncio
async def test_a_player_who_left_cannot_be_paired_later(redis_connection):
    mm = _queue(redis_connection, "replica-a")
    quitter, arrival = MockSession("Quitter", 1200), MockSession("Arrival", 1200)

    await mm.join_queue(quitter)
    await mm.leave_queue(quitter)
    await mm.join_queue(arrival)

    assert await mm.try_match() is None
    assert mm.queue_length == 1
