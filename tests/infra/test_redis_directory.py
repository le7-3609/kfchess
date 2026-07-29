"""The fleet directory against a real Redis.

What this proves and the dict cannot: that an entry written by one replica is
readable by another, and that Redis really does expire it — the property the
in-process version can only simulate with an injected clock.
"""

import asyncio

import pytest

from server.domain.coordination.directory import SeatLocation
from server.infrastructure.coordination.redis_directory import KEY_SEAT, RedisDirectory

pytestmark = pytest.mark.infra


@pytest.mark.asyncio
async def test_a_seat_written_by_one_replica_is_read_by_another(redis_connection):
    """Step 5's reconnect criterion at the directory level: a returning client
    that lands anywhere can be told where its seat is."""
    replica_a = RedisDirectory(redis_connection, replica_id="replica-a")
    replica_b = RedisDirectory(redis_connection, replica_id="replica-b")

    await replica_a.bind_seat(SeatLocation("Alice", "ROOM01", "replica-a"))

    found = await replica_b.find_seat("Alice")
    assert found == SeatLocation("Alice", "ROOM01", "replica-a")
    assert not found.is_on("replica-b"), "replica B must know the seat is not its own"


@pytest.mark.asyncio
async def test_rebinding_replaces_rather_than_duplicates(redis_connection):
    directory = RedisDirectory(redis_connection, replica_id="replica-a")
    await directory.bind_seat(SeatLocation("Alice", "ROOM01", "replica-a"))
    await directory.bind_seat(SeatLocation("Alice", "ROOM02", "replica-b"))

    assert (await directory.find_seat("Alice")).room_id == "ROOM02"


@pytest.mark.asyncio
async def test_a_released_seat_is_gone_fleet_wide(redis_connection):
    directory = RedisDirectory(redis_connection, replica_id="replica-a")
    await directory.bind_seat(SeatLocation("Alice", "ROOM01", "replica-a"))

    await directory.release_seat("Alice")

    assert await directory.find_seat("Alice") is None
    await directory.release_seat("Alice")


@pytest.mark.asyncio
async def test_redis_expires_the_entry_by_itself(redis_connection):
    """The property that bounds a crashed replica's leftovers. Verified with a
    one-second TTL rather than the ten-minute production one, since the
    mechanism under test is Redis's expiry, not the duration chosen for it."""
    directory = RedisDirectory(redis_connection, replica_id="replica-a", ttl_seconds=1)
    await directory.bind_seat(SeatLocation("Alice", "ROOM01", "replica-a"))

    assert await directory.find_seat("Alice") is not None
    await asyncio.sleep(1.2)
    assert await directory.find_seat("Alice") is None


@pytest.mark.asyncio
async def test_the_entry_carries_the_configured_ttl(redis_connection):
    """A key written without one would survive a crash forever."""
    directory = RedisDirectory(redis_connection, replica_id="replica-a", ttl_seconds=600)
    await directory.bind_seat(SeatLocation("Alice", "ROOM01", "replica-a"))

    ttl = await redis_connection.client.ttl(redis_connection.key(KEY_SEAT, "Alice"))

    assert 0 < ttl <= 600


@pytest.mark.asyncio
async def test_room_ownership_round_trips(redis_connection):
    directory = RedisDirectory(redis_connection, replica_id="authority-1")

    await directory.register_room("ROOM01", "authority-1")

    assert await directory.owner_of("ROOM01") == "authority-1"
    await directory.release_room("ROOM01")
    assert await directory.owner_of("ROOM01") is None


@pytest.mark.asyncio
async def test_an_unreadable_entry_reads_as_absent(redis_connection):
    """Corrupt data must not raise into the reconnect path.

    A returning player is better served by "no seat found" — which puts them in
    the lobby — than by an exception that drops the socket.
    """
    directory = RedisDirectory(redis_connection, replica_id="replica-a")
    await redis_connection.client.set(redis_connection.key(KEY_SEAT, "Alice"), "not-json")

    assert await directory.find_seat("Alice") is None
