"""Unit tests for ownership leases, fencing tokens, and rendezvous placement.

The same store contract runs against Redis in
`tests/infra/test_redis_leases.py`, including the failure injection Step 7's
exit criterion calls for. What is pinned here is the arithmetic — placement
stability and token ordering — which is identical whichever store is behind it.
"""

import pytest

from server.application.room_ownership import RoomOwnership
from server.domain.coordination.leases import (
    InProcessLeases,
    Lease,
    rendezvous_owner,
)


def _clock(start: float = 0.0):
    holder = [start]
    return holder, lambda: holder[0]


def test_placement_is_deterministic_across_instances():
    """Every instance computes the same owner from the same live set, which is
    what lets placement work with no coordinator."""
    instances = ["auth-1", "auth-2", "auth-3"]

    first = rendezvous_owner("ROOM01", instances)
    again = rendezvous_owner("ROOM01", list(reversed(instances)))

    assert first == again, "placement must not depend on the order of the set"


def test_placement_spreads_rooms_across_instances():
    instances = [f"auth-{i}" for i in range(4)]

    owners = {rendezvous_owner(f"ROOM{i:04d}", instances) for i in range(200)}

    assert owners == set(instances), "every instance must receive some rooms"


def test_losing_an_instance_moves_only_its_own_rooms():
    """The reason for rendezvous hashing rather than modulo. At 83,000 rooms
    created and finished per second, remapping every room on a scale event is a
    continuous reshuffle the fleet cannot absorb."""
    before = [f"auth-{i}" for i in range(4)]
    after = [instance for instance in before if instance != "auth-2"]
    rooms = [f"ROOM{i:04d}" for i in range(400)]

    moved = [
        room
        for room in rooms
        if rendezvous_owner(room, before) != rendezvous_owner(room, after)
    ]

    assert all(rendezvous_owner(room, before) == "auth-2" for room in moved), (
        "only rooms belonging to the departed instance may move"
    )


def test_placement_with_no_live_instances_has_no_answer():
    assert rendezvous_owner("ROOM01", []) is None


def test_a_newer_claim_supersedes_an_older_one():
    old = Lease("ROOM01", "auth-1", fencing_token=7, expires_at=0.0)
    new = Lease("ROOM01", "auth-2", fencing_token=8, expires_at=0.0)

    assert new.supersedes(old)
    assert not old.supersedes(new)
    assert new.supersedes(None), "any claim beats no claim"


def test_claims_on_different_rooms_are_not_comparable():
    """Comparing them would silently answer a question nobody should be asking."""
    with pytest.raises(ValueError):
        Lease("ROOM01", "auth-1", 1, 0.0).supersedes(Lease("ROOM02", "auth-2", 2, 0.0))


@pytest.mark.asyncio
async def test_a_room_can_only_be_held_by_one_instance():
    leases = InProcessLeases()

    first = await leases.acquire("ROOM01", "auth-1")
    second = await leases.acquire("ROOM01", "auth-2")

    assert first is not None
    assert second is None, "a second instance must not be able to take a held room"


@pytest.mark.asyncio
async def test_reacquiring_your_own_room_is_allowed():
    """A retry after a slow response must not read as a competitor."""
    leases = InProcessLeases()
    first = await leases.acquire("ROOM01", "auth-1")

    again = await leases.acquire("ROOM01", "auth-1")

    assert again is not None
    assert again.fencing_token == first.fencing_token


@pytest.mark.asyncio
async def test_an_expired_lease_is_acquirable_by_someone_else():
    holder, read = _clock()
    leases = InProcessLeases(ttl_seconds=30.0, time_fn=read)
    await leases.acquire("ROOM01", "auth-1")

    holder[0] = 31.0
    taken = await leases.acquire("ROOM01", "auth-2")

    assert taken is not None
    assert taken.owner == "auth-2"


@pytest.mark.asyncio
async def test_the_fencing_token_increases_across_owners():
    """Two instances that both believe they own a room can always be ordered."""
    holder, read = _clock()
    leases = InProcessLeases(ttl_seconds=30.0, time_fn=read)
    first = await leases.acquire("ROOM01", "auth-1")

    holder[0] = 31.0
    second = await leases.acquire("ROOM01", "auth-2")

    assert second.fencing_token > first.fencing_token


@pytest.mark.asyncio
async def test_a_stale_owner_cannot_renew_a_lease_it_lost():
    """The failure a bare EXPIRE would allow: an instance that stalled long
    enough to lose its room pushing the *new* owner's deadline out."""
    holder, read = _clock()
    leases = InProcessLeases(ttl_seconds=30.0, time_fn=read)
    stale = await leases.acquire("ROOM01", "auth-1")

    holder[0] = 31.0
    await leases.acquire("ROOM01", "auth-2")

    assert await leases.renew(stale) is None
    assert await leases.owner_of("ROOM01") == "auth-2"


@pytest.mark.asyncio
async def test_renewing_extends_the_deadline():
    holder, read = _clock()
    leases = InProcessLeases(ttl_seconds=30.0, time_fn=read)
    lease = await leases.acquire("ROOM01", "auth-1")

    holder[0] = 20.0
    renewed = await leases.renew(lease)

    assert renewed.expires_at == 50.0
    holder[0] = 45.0
    assert await leases.owner_of("ROOM01") == "auth-1"


@pytest.mark.asyncio
async def test_releasing_frees_the_room_immediately():
    leases = InProcessLeases()
    lease = await leases.acquire("ROOM01", "auth-1")

    await leases.release(lease)

    assert await leases.owner_of("ROOM01") is None
    assert await leases.acquire("ROOM01", "auth-2") is not None


@pytest.mark.asyncio
async def test_releasing_a_stale_lease_does_not_evict_the_new_owner():
    holder, read = _clock()
    leases = InProcessLeases(ttl_seconds=30.0, time_fn=read)
    stale = await leases.acquire("ROOM01", "auth-1")
    holder[0] = 31.0
    await leases.acquire("ROOM01", "auth-2")

    await leases.release(stale)

    assert await leases.owner_of("ROOM01") == "auth-2"


@pytest.mark.asyncio
async def test_ownership_refuses_a_renewal_interval_at_or_past_the_ttl():
    """Otherwise the lease expires before the loop comes round to renew it."""
    with pytest.raises(ValueError):
        RoomOwnership(
            InProcessLeases(), "auth-1", renewal_interval_seconds=30.0, lease_ttl_seconds=30.0
        )


@pytest.mark.asyncio
async def test_ownership_tracks_what_it_holds():
    ownership = RoomOwnership(InProcessLeases(), "auth-1")

    await ownership.acquire("ROOM01")
    await ownership.acquire("ROOM02")

    assert ownership.owned_room_count == 2
    assert ownership.owns("ROOM01")
    assert set(ownership.owned_rooms()) == {"ROOM01", "ROOM02"}

    await ownership.release("ROOM01")
    assert not ownership.owns("ROOM01")
    assert ownership.owned_room_count == 1


@pytest.mark.asyncio
async def test_a_draining_instance_takes_no_new_rooms_but_keeps_its_own():
    """The SIGTERM contract: stop accepting work, finish what is running."""
    ownership = RoomOwnership(InProcessLeases(), "auth-1")
    await ownership.acquire("ROOM01")

    ownership.begin_draining()

    assert await ownership.acquire("ROOM02") is None
    assert ownership.owns("ROOM01"), "a drain must not abandon a live game"
    assert ownership.draining


@pytest.mark.asyncio
async def test_losing_a_lease_shuts_the_room_down_locally():
    """An instance that keeps computing a room whose lease expired is the second
    authority the design exists to prevent."""
    holder, read = _clock()
    store = InProcessLeases(ttl_seconds=30.0, time_fn=read)
    surrendered = []

    async def on_lost(room_id):
        surrendered.append(room_id)

    ownership = RoomOwnership(store, "auth-1", on_lease_lost=on_lost)
    await ownership.acquire("ROOM01")

    holder[0] = 31.0
    await store.acquire("ROOM01", "auth-2")
    await ownership._renew_all()

    assert surrendered == ["ROOM01"]
    assert not ownership.owns("ROOM01")


@pytest.mark.asyncio
async def test_stopping_releases_every_lease_so_rooms_are_replaceable_at_once():
    """A clean shutdown must not leave rooms unplaceable for a whole TTL."""
    store = InProcessLeases()
    ownership = RoomOwnership(store, "auth-1")
    await ownership.acquire("ROOM01")
    await ownership.start()

    await ownership.stop()

    assert await store.owner_of("ROOM01") is None
