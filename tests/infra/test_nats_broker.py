"""The broker contract against a real NATS with JetStream.

Same expectations as `tests/unit/test_broker.py`, plus the two properties only a
real broker has: that a core subject genuinely spans processes, and that
JetStream really does hold a message until a consumer acknowledges it.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio

from server.infrastructure.broker.nats_broker import NatsBroker

pytestmark = pytest.mark.infra

# Long enough for a local NATS round trip and short enough that a genuine
# failure fails the test rather than hanging it.
_DELIVERY_TIMEOUT = 3.0


async def _await_until(predicate, timeout: float = _DELIVERY_TIMEOUT) -> bool:
    """Poll *predicate* until it holds or the budget runs out.

    Delivery is asynchronous, so an assertion made immediately after a publish
    tests the scheduler rather than the broker.
    """
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


@pytest_asyncio.fixture
async def broker(nats_url):
    connected = NatsBroker(url=nats_url)
    await connected.connect()
    yield connected
    await connected.close()


@pytest_asyncio.fixture
async def second_broker(nats_url):
    """A second, independent connection — two replicas, one NATS."""
    connected = NatsBroker(url=nats_url)
    await connected.connect()
    yield connected
    await connected.close()


def _subject() -> str:
    """A fresh subject per test, so a leftover message never leaks between them."""
    return f"room.{uuid.uuid4().hex[:8]}.events"


@pytest.mark.asyncio
async def test_a_frame_published_on_one_connection_reaches_another(broker, second_broker):
    """Step 6 in one assertion: the publisher and the subscriber are different
    processes as far as NATS is concerned, and neither knows where the other is."""
    subject = _subject()
    received = []

    async def collect(payload):
        received.append(payload)

    await second_broker.subscribe(subject, collect)
    await broker.publish(subject, {"type": "event_move_started", "from": "e2", "to": "e4"})

    assert await _await_until(lambda: len(received) == 1), "frame never crossed the broker"
    assert received[0]["to"] == "e4"


@pytest.mark.asyncio
async def test_every_subscriber_receives_the_frame(broker, second_broker):
    subject = _subject()
    here, there = [], []

    await broker.subscribe(subject, lambda p: _append(here, p))
    await second_broker.subscribe(subject, lambda p: _append(there, p))
    await asyncio.sleep(0.1)

    await broker.publish(subject, {"type": "event_score_updated"})

    assert await _await_until(lambda: here and there)


def _append(sink, payload):
    async def run():
        sink.append(payload)

    return run()


@pytest.mark.asyncio
async def test_unsubscribing_stops_delivery(broker):
    subject = _subject()
    received = []

    subscription = await broker.subscribe(subject, lambda p: _append(received, p))
    await subscription.unsubscribe()
    await broker.publish(subject, {"type": "event_move_started"})
    await asyncio.sleep(0.3)

    assert received == []


@pytest.mark.asyncio
async def test_a_core_publish_with_no_subscriber_is_not_an_error(broker):
    """Fire-and-forget: a room with no spectators publishes into nothing and the
    tick continues. This is the behaviour the reconciliation frame pays for."""
    await broker.publish(_subject(), {"type": "event_move_started"})


@pytest.mark.asyncio
async def test_is_connected_tracks_the_connection(broker, nats_url):
    assert await broker.is_connected() is True

    closing = NatsBroker(url=nats_url)
    assert await closing.is_connected() is False, "a broker not yet connected is not ready"


@pytest.mark.asyncio
async def test_a_durable_message_survives_publication_before_any_consumer(broker):
    """The property the event channel deliberately does not have. A game that
    finished while the worker pool was restarting must still be written."""
    subject = f"game.finished.{uuid.uuid4().hex[:8]}"
    room_id = uuid.uuid4().hex[:6].upper()

    assert await broker.publish_durable(subject, {"room_id": room_id}) is True

    received = []
    await broker.subscribe_durable(
        subject, lambda p: _append(received, p), queue_group=f"test{uuid.uuid4().hex[:6]}"
    )

    assert await _await_until(lambda: len(received) == 1), "JetStream did not replay the message"
    assert received[0]["room_id"] == room_id


@pytest.mark.asyncio
async def test_a_durable_message_goes_to_one_group_member(broker, second_broker):
    """A work queue across two workers, not a broadcast: two persistence workers
    must not each write the same finished game."""
    subject = f"game.finished.{uuid.uuid4().hex[:8]}"
    group = f"test{uuid.uuid4().hex[:6]}"
    first, second = [], []

    await broker.subscribe_durable(subject, lambda p: _append(first, p), queue_group=group)
    await second_broker.subscribe_durable(subject, lambda p: _append(second, p), queue_group=group)
    await asyncio.sleep(0.2)

    await broker.publish_durable(subject, {"room_id": "ABC123"})

    assert await _await_until(lambda: len(first) + len(second) == 1)
    await asyncio.sleep(0.3)
    assert len(first) + len(second) == 1, "the message was delivered to both workers"
