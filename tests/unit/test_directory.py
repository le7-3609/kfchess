"""Unit tests for the fleet directory port and its in-process implementation.

The behaviour pinned here is what `RoomManager.locate_seat` depends on and what
`tests/infra/test_redis_directory.py` re-proves against Redis: an entry can be
found from a keyed lookup, is refreshed rather than duplicated on rewrite, and
expires on its own so a crashed replica's leftovers do not outlive it.
"""

import pytest

from server.domain.coordination.directory import InProcessDirectory, SeatLocation


def _clock(start: float = 0.0):
    holder = [start]
    return holder, lambda: holder[0]


@pytest.mark.asyncio
async def test_a_seat_is_found_by_the_player_who_holds_it():
    directory = InProcessDirectory(replica_id="replica-a")
    await directory.bind_seat(SeatLocation("Alice", "ROOM01", "replica-a"))

    found = await directory.find_seat("Alice")

    assert found == SeatLocation("Alice", "ROOM01", "replica-a")
    assert found.is_on("replica-a")
    assert await directory.find_seat("Bob") is None


@pytest.mark.asyncio
async def test_a_seat_names_the_replica_that_can_serve_it():
    """The whole reason the entry carries a replica: a returning client landing
    on the wrong one has to be routable to the right one."""
    directory = InProcessDirectory(replica_id="replica-a")
    await directory.bind_seat(SeatLocation("Alice", "ROOM01", "replica-b"))

    found = await directory.find_seat("Alice")

    assert not found.is_on("replica-a")
    assert found.replica == "replica-b"


@pytest.mark.asyncio
async def test_rebinding_a_seat_replaces_rather_than_duplicates():
    directory = InProcessDirectory()
    await directory.bind_seat(SeatLocation("Alice", "ROOM01", "replica-a"))
    await directory.bind_seat(SeatLocation("Alice", "ROOM02", "replica-b"))

    assert (await directory.find_seat("Alice")).room_id == "ROOM02"


@pytest.mark.asyncio
async def test_a_released_seat_is_gone():
    directory = InProcessDirectory()
    await directory.bind_seat(SeatLocation("Alice", "ROOM01", "replica-a"))

    await directory.release_seat("Alice")

    assert await directory.find_seat("Alice") is None
    # Releasing twice must stay silent: the disconnect path runs for every
    # closing socket, including players who were never seated.
    await directory.release_seat("Alice")


@pytest.mark.asyncio
async def test_a_seat_expires_on_its_own():
    """A crash deletes nothing, so the store has to forget by itself."""
    holder, read = _clock()
    directory = InProcessDirectory(ttl_seconds=600, time_fn=read)
    await directory.bind_seat(SeatLocation("Alice", "ROOM01", "replica-a"))

    holder[0] = 599.0
    assert await directory.find_seat("Alice") is not None

    holder[0] = 600.0
    assert await directory.find_seat("Alice") is None


@pytest.mark.asyncio
async def test_a_room_is_found_by_its_owner():
    directory = InProcessDirectory()
    await directory.register_room("ROOM01", "authority-3")

    assert await directory.owner_of("ROOM01") == "authority-3"
    assert await directory.owner_of("NOPE01") is None


@pytest.mark.asyncio
async def test_a_room_entry_expires_and_is_releasable():
    holder, read = _clock()
    directory = InProcessDirectory(ttl_seconds=600, time_fn=read)
    await directory.register_room("ROOM01", "authority-3")
    await directory.register_room("ROOM02", "authority-3")

    await directory.release_room("ROOM01")
    assert await directory.owner_of("ROOM01") is None

    holder[0] = 601.0
    assert await directory.owner_of("ROOM02") is None
