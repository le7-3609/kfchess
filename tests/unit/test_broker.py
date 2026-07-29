"""Unit tests for the broker port and its in-process implementation.

The same expectations run against NATS in `tests/infra/test_nats_broker.py`.
Anything asserted here is a property of the *contract*, not of the dict-based
implementation — which is why the two files test the same things.
"""

import pytest

from server.domain.coordination.broker import (
    InProcessBroker,
    room_commands_subject,
    room_events_subject,
    session_frames_subject,
)

SUBJECT = "room.ABC123.events"


def _collector(sink):
    async def handle(payload):
        sink.append(payload)

    return handle


def test_subjects_are_built_from_their_identifiers():
    assert room_events_subject("ABC123") == "room.ABC123.events"
    assert room_commands_subject("ABC123") == "room.ABC123.commands"
    assert session_frames_subject("Alice") == "session.Alice.frames"


@pytest.mark.asyncio
async def test_a_published_frame_reaches_every_subscriber():
    """Fan-out, not a work queue: both players and every spectator of a room
    receive the same event."""
    broker = InProcessBroker()
    first, second = [], []
    await broker.subscribe(SUBJECT, _collector(first))
    await broker.subscribe(SUBJECT, _collector(second))

    await broker.publish(SUBJECT, {"type": "event_move_started"})

    assert first == second == [{"type": "event_move_started"}]


@pytest.mark.asyncio
async def test_a_frame_on_another_subject_is_not_delivered():
    broker = InProcessBroker()
    received = []
    await broker.subscribe(SUBJECT, _collector(received))

    await broker.publish("room.OTHER1.events", {"type": "event_move_started"})

    assert received == []


@pytest.mark.asyncio
async def test_unsubscribing_stops_delivery_and_is_idempotent():
    broker = InProcessBroker()
    received = []
    subscription = await broker.subscribe(SUBJECT, _collector(received))

    await subscription.unsubscribe()
    await subscription.unsubscribe()
    await broker.publish(SUBJECT, {"type": "event_move_started"})

    assert received == []


@pytest.mark.asyncio
async def test_a_raising_subscriber_does_not_stop_the_others():
    """A subscriber runs inside a room's tick. One that raises must not abandon
    the frame for everyone else — the same reason `EventBus.publish` contains
    subscriber exceptions in `core`."""
    broker = InProcessBroker()
    received = []

    async def explode(_payload):
        raise RuntimeError("subscriber is broken")

    await broker.subscribe(SUBJECT, explode)
    await broker.subscribe(SUBJECT, _collector(received))

    await broker.publish(SUBJECT, {"type": "event_move_started"})

    assert received == [{"type": "event_move_started"}]


@pytest.mark.asyncio
async def test_publishing_with_no_subscriber_is_silently_fine():
    """Fire-and-forget means exactly that: a room with no spectators publishes
    into nothing and the tick continues."""
    broker = InProcessBroker()

    await broker.publish(SUBJECT, {"type": "event_move_started"})


@pytest.mark.asyncio
async def test_a_durable_message_goes_to_one_group_member_not_all():
    """A work queue. Delivering a finished game to every worker would write it
    once per worker."""
    broker = InProcessBroker()
    first, second = [], []
    await broker.subscribe_durable("game.finished", _collector(first), "persisters")
    await broker.subscribe_durable("game.finished", _collector(second), "persisters")

    await broker.publish_durable("game.finished", {"room_id": "ABC123"})

    assert len(first) + len(second) == 1


@pytest.mark.asyncio
async def test_a_durable_message_published_before_any_worker_is_still_delivered():
    """The property that separates the durable channel from the other one: a
    game that finished while the worker pool was restarting must not be lost."""
    broker = InProcessBroker()

    assert await broker.publish_durable("game.finished", {"room_id": "ABC123"}) is True

    received = []
    await broker.subscribe_durable("game.finished", _collector(received), "persisters")

    assert received == [{"room_id": "ABC123"}]


@pytest.mark.asyncio
async def test_the_in_process_broker_reports_itself_reachable():
    """It backs the readiness check, and a process is always able to reach
    itself — so this must be True rather than an unimplemented stub."""
    assert await InProcessBroker().is_connected() is True
