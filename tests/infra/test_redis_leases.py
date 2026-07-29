"""Ownership leases against a real Redis, including the failure injection.

Step 7's exit criterion is explicit that reading the code is not enough: an
instance holding live rooms is killed, and what must be *shown* is the leases
expiring, another instance acquiring the ids, and the damage bounded to the
rooms the dead instance owned.

`test_killing_an_owner_bounds_the_damage_to_its_own_rooms` is that test. A killed
instance is modelled as one that stops renewing and never releases — which is
exactly what a `SIGKILL`, a severed network or a hung process looks like from
Redis's side, and is the only thing any survivor can observe about it.
"""

import asyncio

import pytest
import pytest_asyncio

from server.application.room_ownership import RoomOwnership
from server.infrastructure.coordination.redis_leases import KEY_OWNER, RedisLeaseStore

pytestmark = pytest.mark.infra

# Short enough to watch expire inside a test, long enough that a local Redis
# round trip cannot accidentally exceed it. The production values (30s TTL,
# 10s renewal) are the same mechanism at a different scale.
_TEST_TTL = 1.0
_TEST_RENEWAL = 0.3


@pytest_asyncio.fixture
async def store(redis_connection):
    return RedisLeaseStore(redis_connection, ttl_seconds=_TEST_TTL)


@pytest.mark.asyncio
async def test_only_one_instance_can_hold_a_room(store):
    first = await store.acquire("ROOM01", "auth-1")
    second = await store.acquire("ROOM01", "auth-2")

    assert first is not None
    assert second is None
    assert await store.owner_of("ROOM01") == "auth-1"


@pytest.mark.asyncio
async def test_a_race_between_many_instances_produces_exactly_one_owner(store):
    """Twenty instances reach for one room at once. `SET NX` in a script is what
    makes this decidable rather than a coin flip resolved twice."""
    claims = await asyncio.gather(
        *(store.acquire("ROOM01", f"auth-{i}") for i in range(20))
    )

    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1, f"{len(winners)} instances believed they owned one room"
    assert await store.owner_of("ROOM01") == winners[0].owner


@pytest.mark.asyncio
async def test_the_fencing_token_increases_across_generations(store):
    first = await store.acquire("ROOM01", "auth-1")
    await asyncio.sleep(_TEST_TTL + 0.2)
    second = await store.acquire("ROOM01", "auth-2")

    assert second.fencing_token > first.fencing_token
    assert second.supersedes(first)


@pytest.mark.asyncio
async def test_the_token_survives_the_room_going_briefly_unowned(store, redis_connection):
    """Why the counter lives in its own, non-expiring key. If it reset with the
    owner key, two generations would compare equal — and the comparison the
    token exists to make decidable would silently stop working."""
    first = await store.acquire("ROOM01", "auth-1")
    await asyncio.sleep(_TEST_TTL + 0.2)
    assert await store.owner_of("ROOM01") is None

    second = await store.acquire("ROOM01", "auth-2")

    assert second.fencing_token == first.fencing_token + 1


@pytest.mark.asyncio
async def test_renewing_keeps_a_room_past_its_original_deadline(store):
    lease = await store.acquire("ROOM01", "auth-1")

    for _ in range(4):
        await asyncio.sleep(_TEST_RENEWAL)
        lease = await store.renew(lease)
        assert lease is not None

    assert await store.owner_of("ROOM01") == "auth-1"


@pytest.mark.asyncio
async def test_a_stale_owner_cannot_renew_a_lease_someone_else_took(store):
    """The failure a read-then-write renewal would allow."""
    stale = await store.acquire("ROOM01", "auth-1")
    await asyncio.sleep(_TEST_TTL + 0.2)
    await store.acquire("ROOM01", "auth-2")

    assert await store.renew(stale) is None
    assert await store.owner_of("ROOM01") == "auth-2", "the new owner's claim survived"


@pytest.mark.asyncio
async def test_a_stale_owner_cannot_release_the_new_owners_room(store):
    stale = await store.acquire("ROOM01", "auth-1")
    await asyncio.sleep(_TEST_TTL + 0.2)
    await store.acquire("ROOM01", "auth-2")

    await store.release(stale)

    assert await store.owner_of("ROOM01") == "auth-2"


@pytest.mark.asyncio
async def test_the_lease_key_carries_a_ttl(store, redis_connection):
    """A lease written without one would outlive the instance holding it, and
    the room would never be re-placeable."""
    await store.acquire("ROOM01", "auth-1")

    ttl = await redis_connection.client.pttl(redis_connection.key(KEY_OWNER, "ROOM01"))

    assert 0 < ttl <= _TEST_TTL * 1000


@pytest.mark.asyncio
async def test_killing_an_owner_bounds_the_damage_to_its_own_rooms(store):
    """Step 7's exit criterion, as a deliberate failure injection.

    Two instances hold three rooms each. One is killed — modelled as ceasing to
    renew without releasing, which is what a SIGKILL, a severed network or a hung
    process all look like from Redis. What is asserted is not that the games
    survived (they do not, and that is the recorded decision) but that the
    failure is *bounded*: the survivor's rooms are untouched, and the dead
    instance's ids become acquirable.
    """
    victim = RoomOwnership(
        store, "auth-victim", renewal_interval_seconds=_TEST_RENEWAL, lease_ttl_seconds=_TEST_TTL
    )
    survivor = RoomOwnership(
        store, "auth-survivor", renewal_interval_seconds=_TEST_RENEWAL, lease_ttl_seconds=_TEST_TTL
    )
    victim_rooms = ["DEAD01", "DEAD02", "DEAD03"]
    survivor_rooms = ["LIVE01", "LIVE02", "LIVE03"]

    for room_id in victim_rooms:
        assert await victim.acquire(room_id) is not None
    for room_id in survivor_rooms:
        assert await survivor.acquire(room_id) is not None
    await victim.start()
    await survivor.start()

    # Both alive: nobody else can take anything.
    await asyncio.sleep(_TEST_RENEWAL * 2)
    for room_id in victim_rooms + survivor_rooms:
        assert await store.acquire(room_id, "auth-newcomer") is None

    # The kill. No release, no final renewal — the process is simply gone.
    victim._renewal_task.cancel()
    await asyncio.sleep(_TEST_TTL + _TEST_RENEWAL)

    taken_over = []
    for room_id in victim_rooms:
        lease = await store.acquire(room_id, "auth-newcomer")
        if lease is not None:
            taken_over.append(room_id)

    assert taken_over == victim_rooms, "every dead instance's room must become acquirable"

    for room_id in survivor_rooms:
        assert await store.owner_of(room_id) == "auth-survivor", (
            f"{room_id} belonged to the surviving instance and must be untouched"
        )
        assert await store.acquire(room_id, "auth-newcomer") is None

    await survivor.stop()


@pytest.mark.asyncio
async def test_a_surviving_instance_notices_it_lost_a_room_and_shuts_it_down(store):
    """The other half of the same failure: an instance that stalls long enough
    for its lease to lapse must give the room up locally, not keep computing a
    game a second authority now owns."""
    surrendered = []

    async def on_lost(room_id):
        surrendered.append(room_id)

    stalled = RoomOwnership(
        store,
        "auth-stalled",
        on_lease_lost=on_lost,
        renewal_interval_seconds=_TEST_RENEWAL,
        lease_ttl_seconds=_TEST_TTL,
    )
    await stalled.acquire("ROOM01")

    # The stall: long enough for the TTL to lapse and a competitor to take it.
    await asyncio.sleep(_TEST_TTL + 0.2)
    assert await store.acquire("ROOM01", "auth-other") is not None

    await stalled._renew_all()

    assert surrendered == ["ROOM01"]
    assert not stalled.owns("ROOM01")


@pytest.mark.asyncio
async def test_a_clean_shutdown_frees_rooms_immediately(store):
    """A drained instance's rooms must be re-placeable at once, not after a full
    TTL during which nobody may take them."""
    leaving = RoomOwnership(
        store, "auth-leaving", renewal_interval_seconds=_TEST_RENEWAL, lease_ttl_seconds=_TEST_TTL
    )
    await leaving.acquire("ROOM01")
    await leaving.start()

    await leaving.stop()

    assert await store.owner_of("ROOM01") is None
    assert await store.acquire("ROOM01", "auth-next") is not None


@pytest.mark.asyncio
async def test_a_draining_instance_keeps_renewing_what_it_already_holds(store):
    """SIGTERM means stop taking work, not stop working. Games last 30 to 90
    seconds and must be allowed to finish."""
    draining = RoomOwnership(
        store, "auth-draining", renewal_interval_seconds=_TEST_RENEWAL, lease_ttl_seconds=_TEST_TTL
    )
    await draining.acquire("ROOM01")
    await draining.start()

    draining.begin_draining()
    await asyncio.sleep(_TEST_TTL + _TEST_RENEWAL)

    assert await store.owner_of("ROOM01") == "auth-draining", (
        "a draining instance abandoned a live room"
    )
    assert await draining.acquire("ROOM02") is None, "and must take no new ones"

    await draining.stop()
