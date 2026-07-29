"""Unit tests for the gateway relay.

What is pinned here is the bookkeeping, because that is where the leaks are: a
room subscription shared by several local clients must survive one of them
leaving and must not survive the last one, and a frame for a socket that has
already closed must be dropped rather than raised.
"""

import pytest

from server.application.gateway_relay import GatewayRelay
from server.domain.coordination.broker import (
    InProcessBroker,
    room_commands_subject,
    room_events_subject,
    session_frames_subject,
)


class FakeSession:
    """A seat that records what was written to it instead of holding a socket."""

    def __init__(self, username: str, fails: bool = False):
        self.username = username
        self.websocket = object()
        self.sent = []
        self._fails = fails

    async def send(self, message):
        if self._fails:
            raise ConnectionError("socket is gone")
        self.sent.append(message)


@pytest.mark.asyncio
async def test_a_frame_addressed_to_a_player_reaches_their_socket():
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    alice = FakeSession("Alice")
    await relay.attach_session(alice)

    await broker.publish(session_frames_subject("Alice"), {"type": "game_start"})

    assert alice.sent == [{"type": "game_start"}]


@pytest.mark.asyncio
async def test_a_frame_for_another_player_is_not_delivered_here():
    """The per-recipient subject exists precisely so one player's snapshot —
    carrying their selection and legal moves — never reaches the opponent."""
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    alice = FakeSession("Alice")
    await relay.attach_session(alice)

    await broker.publish(session_frames_subject("Bob"), {"type": "game_state"})

    assert alice.sent == []


@pytest.mark.asyncio
async def test_a_rooms_event_stream_reaches_every_local_member():
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    alice, bob = FakeSession("Alice"), FakeSession("Bob")
    await relay.attach_session(alice)
    await relay.attach_session(bob)
    await relay.join_room("ABC123", "Alice")
    await relay.join_room("ABC123", "Bob")

    await broker.publish(room_events_subject("ABC123"), {"type": "event_move_started"})

    assert alice.sent == bob.sent == [{"type": "event_move_started"}]


@pytest.mark.asyncio
async def test_two_local_members_share_one_room_subscription():
    """Otherwise a busy gateway holds a subscription per player rather than per
    room, and each event is delivered to it twice."""
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    await relay.attach_session(FakeSession("Alice"))
    await relay.attach_session(FakeSession("Bob"))

    await relay.join_room("ABC123", "Alice")
    await relay.join_room("ABC123", "Bob")

    assert len(broker._handlers[room_events_subject("ABC123")]) == 1


@pytest.mark.asyncio
async def test_one_member_leaving_does_not_cut_off_the_others():
    """A spectator closing their tab must not stop the players' event stream."""
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    alice, viewer = FakeSession("Alice"), FakeSession("Viewer")
    await relay.attach_session(alice)
    await relay.attach_session(viewer)
    await relay.join_room("ABC123", "Alice")
    await relay.join_room("ABC123", "Viewer")

    await relay.leave_room("ABC123", "Viewer")
    await broker.publish(room_events_subject("ABC123"), {"type": "event_move_started"})

    assert alice.sent == [{"type": "event_move_started"}]
    assert viewer.sent == []


@pytest.mark.asyncio
async def test_the_last_member_leaving_drops_the_subscription():
    """At 83,000 rooms finishing per second, a subscription leaked per room is
    the fleet's memory gone in under a minute."""
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    await relay.attach_session(FakeSession("Alice"))
    await relay.join_room("ABC123", "Alice")

    await relay.leave_room("ABC123", "Alice")

    assert broker._handlers[room_events_subject("ABC123")] == []


@pytest.mark.asyncio
async def test_detaching_a_session_drops_its_room_memberships_too():
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    alice = FakeSession("Alice")
    await relay.attach_session(alice)
    await relay.join_room("ABC123", "Alice")

    await relay.detach_session(alice)

    assert broker._handlers[session_frames_subject("Alice")] == []
    assert broker._handlers[room_events_subject("ABC123")] == []


@pytest.mark.asyncio
async def test_a_forwarded_command_carries_the_gateway_verified_identity():
    """The authority has no socket to authenticate against, so the username has
    to come from the gateway's own handshake — never from the client's frame."""
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    received = []

    async def collect(payload):
        received.append(payload)

    await broker.subscribe(room_commands_subject("ABC123"), collect)

    await relay.forward_command(
        "ABC123", "Alice", {"type": "move", "from": "e2", "to": "e4", "username": "Mallory"}
    )

    assert received[0]["username"] == "Alice", "a client-supplied username must be overridden"
    assert received[0]["from"] == "e2"


@pytest.mark.asyncio
async def test_a_frame_for_a_closed_socket_is_dropped_not_raised():
    """A frame arriving a millisecond after a socket closed is the normal end of
    every game, not a fault worth propagating into the broker's callback."""
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    await relay.attach_session(FakeSession("Alice", fails=True))

    await broker.publish(session_frames_subject("Alice"), {"type": "game_start"})


@pytest.mark.asyncio
async def test_reattaching_the_same_identity_does_not_duplicate_the_subscription():
    """A reconnect binds a new socket to the same player; the subscription is
    keyed by identity and was already correct."""
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    await relay.attach_session(FakeSession("Alice"))
    returning = FakeSession("Alice")
    await relay.attach_session(returning)

    await broker.publish(session_frames_subject("Alice"), {"type": "game_state"})

    assert len(broker._handlers[session_frames_subject("Alice")]) == 1
    assert returning.sent == [{"type": "game_state"}], "frames follow the new socket"


@pytest.mark.asyncio
async def test_closing_the_relay_drops_everything_it_holds():
    broker = InProcessBroker()
    relay = GatewayRelay(broker)
    await relay.attach_session(FakeSession("Alice"))
    await relay.join_room("ABC123", "Alice")

    await relay.close()

    assert broker._handlers[session_frames_subject("Alice")] == []
    assert broker._handlers[room_events_subject("ABC123")] == []
