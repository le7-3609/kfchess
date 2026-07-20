"""Unit tests for MatchmakingQueue and ELO matching logic."""

import pytest
import pytest_asyncio
from server.domain.matchmaking.queue import MatchmakingQueue


class MockSession:
    def __init__(self, username: str, elo: int):
        self.username = username
        self.elo = elo


@pytest.mark.asyncio
async def test_join_and_leave_queue():
    mm = MatchmakingQueue(max_elo_diff=100)
    s1 = MockSession("Alice", 1200)

    await mm.join_queue(s1)
    assert mm.queue_length == 1

    # Joining again does not duplicate
    await mm.join_queue(s1)
    assert mm.queue_length == 1

    await mm.leave_queue(s1)
    assert mm.queue_length == 0


@pytest.mark.asyncio
async def test_try_match_within_elo_bound():
    mm = MatchmakingQueue(max_elo_diff=100)
    s1 = MockSession("Alice", 1200)
    s2 = MockSession("Bob", 1250)

    await mm.join_queue(s1)
    await mm.join_queue(s2)

    match = await mm.try_match()
    assert match is not None
    m1, m2 = match
    assert {m1.username, m2.username} == {"Alice", "Bob"}
    # Verify synchronous eviction: queue is now empty
    assert mm.queue_length == 0


@pytest.mark.asyncio
async def test_try_match_outside_elo_bound():
    mm = MatchmakingQueue(max_elo_diff=100)
    s1 = MockSession("Alice", 1200)
    s2 = MockSession("Bob", 1350)  # Diff = 150 > 100

    await mm.join_queue(s1)
    await mm.join_queue(s2)

    match = await mm.try_match()
    assert match is None
    assert mm.queue_length == 2


@pytest.mark.asyncio
async def test_check_timeouts():
    clock = [100.0]

    def mock_time():
        return clock[0]

    mm = MatchmakingQueue(timeout_seconds=60.0, time_fn=mock_time)
    s1 = MockSession("Alice", 1200)
    s2 = MockSession("Bob", 1200)

    await mm.join_queue(s1)
    clock[0] += 30.0  # Bob joins 30s later
    await mm.join_queue(s2)

    clock[0] += 40.0  # Total 70s for Alice, 40s for Bob
    timed_out = await mm.check_timeouts()

    assert len(timed_out) == 1
    assert timed_out[0].username == "Alice"
    assert mm.queue_length == 1


@pytest.mark.asyncio
async def test_leave_queue_reports_whether_the_player_was_queued():
    """The disconnect path calls this for every closing socket, so a player who
    never queued must be a silent no-op rather than a phantom removal.
    """
    mm = MatchmakingQueue()
    session = MockSession("Alice", 1200)

    assert await mm.leave_queue(session) is False

    await mm.join_queue(session)
    assert await mm.leave_queue(session) is True
    assert mm.queue_length == 0


@pytest.mark.asyncio
async def test_leaving_the_queue_prevents_a_later_pairing():
    mm = MatchmakingQueue(max_elo_diff=100)
    quitter, arrival = MockSession("Quitter", 1200), MockSession("Arrival", 1200)

    await mm.join_queue(quitter)
    await mm.leave_queue(quitter)
    await mm.join_queue(arrival)

    assert await mm.try_match() is None
    assert mm.queue_length == 1
