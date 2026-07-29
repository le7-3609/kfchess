"""The room lifecycle under ownership leases.

`tests/unit/test_leases.py` pins the lease mechanism — acquire, renew, fence,
drain — in isolation. This file pins the thing that makes the mechanism matter:
that `RoomManager` actually *drives* it, so the set of rooms registered here and
the set of leases held are the same set. A lease store nobody consults is
indistinguishable from no lease store at all.

`tests/infra/test_redis_leases.py` runs the failure injection against real
Redis. Everything here runs against `InProcessLeases`, which is a real
implementation of the same contract and not a stub.
"""

import pytest

from server.application.room_manager import RoomManager, RoomPlacementError
from server.application.room_ownership import RoomOwnership
from server.application.room_use_case import RoomUseCase
from server.domain.coordination.leases import InProcessLeases
from server.domain.matchmaking.queue import MatchmakingQueue
from server.domain.room.game_room import RoomState
from server.infrastructure.observability.metrics import MetricsRegistry, default_registry
from server.infrastructure.observability.server_metrics import (
    METRIC_ROOMS_OWNED,
    ServerMetrics,
    server_metrics,
)

INSTANCE = "auth-1"
OTHER_INSTANCE = "auth-2"


class MockSession:
    def __init__(self, username: str):
        self.username = username
        self.sent_messages = []
        self.connected = True
        self.color = None

    def assign_color(self, color: str):
        self.color = color

    def disconnect(self):
        self.connected = False

    async def send(self, msg):
        self.sent_messages.append(msg)


def _clock(start: float = 0.0):
    holder = [start]
    return holder, lambda: holder[0]


def _owned_manager(store=None, instance_id: str = INSTANCE):
    """A manager wired to ownership exactly as `app_runner` wires it."""
    store = store if store is not None else InProcessLeases()
    manager = RoomManager()
    ownership = RoomOwnership(
        store=store,
        instance_id=instance_id,
        on_lease_lost=manager.surrender_room,
        metrics=ServerMetrics(MetricsRegistry()),
    )
    manager.attach_ownership(ownership)
    return manager, ownership, store


@pytest.mark.asyncio
async def test_creating_a_room_takes_its_lease():
    """The registry and the leases must not be able to disagree."""
    manager, ownership, store = _owned_manager()

    room_id = await manager.create_room(MockSession("Alice"))

    assert ownership.owns(room_id)
    assert await store.owner_of(room_id) == INSTANCE
    assert ownership.owned_room_count == manager.room_count


@pytest.mark.asyncio
async def test_an_id_another_instance_holds_is_never_handed_out(monkeypatch):
    """At five million concurrent rooms drawn from 36^6 ids, colliding with a
    room on another instance is routine — and seating on top of it would give
    one room id two divergent histories."""
    store = InProcessLeases()
    await store.acquire("TAKEN1", OTHER_INSTANCE)
    manager, ownership, _ = _owned_manager(store)

    candidates = iter(["TAKEN1", "FREE01"])
    monkeypatch.setattr(RoomManager, "_generate_room_id", staticmethod(lambda: next(candidates)))

    room_id = await manager.create_room(MockSession("Alice"))

    assert room_id == "FREE01", "a contended id must be retried, not taken"
    assert await store.owner_of("TAKEN1") == OTHER_INSTANCE, "the holder keeps its room"
    assert ownership.owned_rooms() == ["FREE01"]


@pytest.mark.asyncio
async def test_a_finished_room_releases_its_lease_at_once():
    """Waiting out a 30-second TTL would leave a finished id unplaceable for
    longer than a whole game lasts."""
    manager, ownership, store = _owned_manager()
    room_id = await manager.create_room(MockSession("Alice"))

    manager.remove_room(room_id)
    await manager.drain_directory_writes()

    assert not ownership.owns(room_id)
    assert await store.owner_of(room_id) is None


@pytest.mark.asyncio
async def test_losing_a_lease_tears_the_room_down_here():
    """The whole point of the mechanism: an instance whose lease lapsed stops
    computing the room rather than becoming a second authority for it."""
    holder, read = _clock()
    store = InProcessLeases(ttl_seconds=30.0, time_fn=read)
    manager, ownership, _ = _owned_manager(store)
    room_id = await manager.create_room(MockSession("Alice"))
    manager.join_room(room_id, MockSession("Bob"))
    room = manager.get_room(room_id)
    await room.start()

    holder[0] = 31.0
    await store.acquire(room_id, OTHER_INSTANCE)
    await ownership._renew_all()

    assert manager.get_room(room_id) is None, "the room must not still be computed here"
    assert manager.room_count == 0
    assert not ownership.owns(room_id)
    assert room.state == RoomState.FINISHED
    assert room._runner is None, "a surrendered room must stop ticking, not just unindex"


@pytest.mark.asyncio
async def test_a_surrendered_rooms_players_are_free_to_start_another():
    """Players are dropped into a fresh game rather than having the position
    reconstructed (Section 15) — which only works if their seat index goes too."""
    holder, read = _clock()
    store = InProcessLeases(ttl_seconds=30.0, time_fn=read)
    manager, ownership, _ = _owned_manager(store)
    white = MockSession("Alice")
    room_id = await manager.create_room(white)

    holder[0] = 31.0
    await store.acquire(room_id, OTHER_INSTANCE)
    await ownership._renew_all()

    assert manager.find_room_by_session(white) is None
    assert manager.find_room_by_username("Alice") is None


@pytest.mark.asyncio
async def test_surrendering_a_room_this_instance_no_longer_holds_is_a_no_op():
    """A lease can lapse on a room that already finished; the callback still
    fires, and must not raise on the way through."""
    manager, ownership, _ = _owned_manager()
    room_id = await manager.create_room(MockSession("Alice"))
    manager.remove_room(room_id)

    await manager.surrender_room(room_id)

    assert manager.room_count == 0


@pytest.mark.asyncio
async def test_a_draining_instance_starts_no_new_room():
    """SIGTERM stops this instance taking work it may not outlive."""
    manager, ownership, _ = _owned_manager()
    ownership.begin_draining()

    with pytest.raises(RoomPlacementError):
        await manager.create_room(MockSession("Alice"))

    assert manager.room_count == 0


@pytest.mark.asyncio
async def test_a_draining_instance_answers_the_client_rather_than_failing():
    """A refused placement is this client's answer — their retry lands on a
    replica that can serve it — not a traceback out of the frame handler."""
    manager, ownership, _ = _owned_manager()
    ownership.begin_draining()
    use_case = RoomUseCase(room_manager=manager, matchmaker=MatchmakingQueue())

    result = await use_case.create(MockSession("Alice"))

    assert not result.is_ok
    assert "shutting down" in result.error


@pytest.mark.asyncio
async def test_a_room_created_without_ownership_still_works():
    """No lease store is a real configuration — one process contends with
    nobody — and it must not become a required dependency by accident."""
    manager = RoomManager()

    room_id = await manager.create_room(MockSession("Alice"))

    assert manager.get_room(room_id) is not None


@pytest.mark.asyncio
async def test_the_composition_root_wires_ownership_into_the_lifecycle():
    """What was missing was never the mechanism but the call site: leases were
    implemented, tested, and constructed by nothing. This asserts the wiring
    itself, since every test above could pass with the server still building no
    ownership at all."""
    from server.presentation.app_runner import ServerSettings, build_room_ownership

    manager = RoomManager()
    ownership = build_room_ownership(ServerSettings(instance_id=INSTANCE), None, manager)
    try:
        room_id = await manager.create_room(MockSession("Alice"))

        assert ownership.owns(room_id), "the manager must be taking leases through it"
        assert f"{METRIC_ROOMS_OWNED} 1" in default_registry().render()
    finally:
        server_metrics().unbind_live_gauges()


@pytest.mark.asyncio
async def test_ownership_reports_the_metrics_the_autoscaler_reads():
    """These three series were declared and fed by nothing, which renders
    identically to a fleet that is holding no rooms and losing no leases."""
    holder, read = _clock()
    store = InProcessLeases(ttl_seconds=30.0, time_fn=read)
    registry = MetricsRegistry()
    metrics = ServerMetrics(registry)
    manager = RoomManager()
    ownership = RoomOwnership(
        store=store, instance_id=INSTANCE, on_lease_lost=manager.surrender_room, metrics=metrics
    )
    manager.attach_ownership(ownership)
    metrics.bind_rooms_owned(lambda: ownership.owned_room_count)

    room_id = await manager.create_room(MockSession("Alice"))
    assert metrics.lease_acquisitions.value == 1
    assert f"{METRIC_ROOMS_OWNED} 1" in registry.render()

    holder[0] = 31.0
    await store.acquire(room_id, OTHER_INSTANCE)
    await ownership._renew_all()

    assert metrics.lease_failovers.value == 1
    assert f"{METRIC_ROOMS_OWNED} 0" in registry.render()
