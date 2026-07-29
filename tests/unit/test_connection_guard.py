"""Unit tests for the WebSocket tier's admission control.

Each of the four budgets replaces something that is unbounded without it, so
the tests assert both that a refusal happens and that ordinary play never
provokes one.
"""

import pytest

from server.presentation.connection_guard import ConnectionGuard


class _FakeSocket:
    """A stand-in exposing the attributes websockets puts on a connection."""

    def __init__(self, peer="203.0.113.5", headers=None):
        self.remote_address = (peer, 54321)
        self.request_headers = headers or {}


def test_a_connection_within_budget_is_admitted():
    guard = ConnectionGuard(connections_per_minute=2)

    assert guard.admit_connection("1.2.3.4").allowed
    assert guard.admit_connection("1.2.3.4").allowed


def test_connections_beyond_the_per_ip_budget_are_refused_with_a_reason():
    """Each auth attempt costs a bcrypt verification, so unlimited connections
    from one host is unlimited CPU from one host."""
    guard = ConnectionGuard(connections_per_minute=1)
    guard.admit_connection("1.2.3.4")

    decision = guard.admit_connection("1.2.3.4")

    assert not decision.allowed
    assert decision.reason


def test_the_connection_budget_is_per_source_address():
    guard = ConnectionGuard(connections_per_minute=1)
    guard.admit_connection("1.2.3.4")

    assert guard.admit_connection("5.6.7.8").allowed


def test_frames_are_admitted_up_to_the_burst_then_refused():
    guard = ConnectionGuard(frames_per_second=1.0, frame_burst=3)

    assert all(guard.admit_frame("alice").allowed for _ in range(3))
    assert not guard.admit_frame("alice").allowed


def test_the_frame_budget_is_per_session():
    guard = ConnectionGuard(frames_per_second=1.0, frame_burst=1)
    guard.admit_frame("alice")

    assert guard.admit_frame("bob").allowed


def test_forgetting_a_session_releases_its_bucket():
    """A closed socket must not leave an entry behind, or the map grows with
    every connection the server has ever accepted."""
    guard = ConnectionGuard(frames_per_second=1.0, frame_burst=1)
    guard.admit_frame("alice")
    assert not guard.admit_frame("alice").allowed

    guard.forget_session("alice")

    assert guard.admit_frame("alice").allowed


def test_room_creation_is_refused_once_the_user_holds_too_many():
    guard = ConnectionGuard(concurrent_rooms_per_user=2)

    assert guard.admit_room_creation("alice", current_room_count=1).allowed
    decision = guard.admit_room_creation("alice", current_room_count=2)

    assert not decision.allowed
    assert "open rooms" in decision.reason


def test_room_creation_is_refused_once_the_rate_is_exceeded():
    """The concurrency cap alone would not stop a loop that creates and abandons
    rooms as fast as the loop turns."""
    guard = ConnectionGuard(rooms_per_minute=2, concurrent_rooms_per_user=100)

    assert guard.admit_room_creation("alice", 0).allowed
    assert guard.admit_room_creation("alice", 0).allowed
    assert not guard.admit_room_creation("alice", 0).allowed


def test_login_backoff_permits_a_connections_full_attempt_budget():
    """One connection is allowed three attempts, so a typo must never be
    delayed; the fourth attempt is what pays."""
    guard = ConnectionGuard()

    for _ in range(3):
        assert guard.check_login_backoff("alice").allowed
        guard.record_login_failure("alice")

    assert not guard.check_login_backoff("alice").allowed


def test_a_successful_login_clears_the_backoff():
    guard = ConnectionGuard()
    for _ in range(5):
        guard.record_login_failure("alice")
    assert not guard.check_login_backoff("alice").allowed

    guard.record_login_success("alice")

    assert guard.check_login_backoff("alice").allowed


def test_client_address_uses_the_peer_when_no_proxy_is_trusted():
    guard = ConnectionGuard()

    address = guard.client_address(
        _FakeSocket(peer="10.0.0.7", headers={"X-Forwarded-For": "1.1.1.1"})
    )

    assert address == "10.0.0.7"


def test_client_address_honours_a_trusted_proxys_forwarded_header():
    guard = ConnectionGuard(trusted_proxies=["10.0.0.7"])

    address = guard.client_address(
        _FakeSocket(peer="10.0.0.7", headers={"X-Forwarded-For": "203.0.113.9"})
    )

    assert address == "203.0.113.9"


def test_client_address_tolerates_a_socket_exposing_neither_attribute():
    """Test doubles and websockets releases differ in what they expose; an
    unknown address must degrade to one shared bucket, never to a crash."""

    class _Bare:
        pass

    assert ConnectionGuard().client_address(_Bare()) == "unknown"


@pytest.mark.parametrize("attribute", ["request_headers", "request"])
def test_forwarded_header_is_read_across_websockets_layouts(attribute):
    """websockets 12 exposes request_headers on the connection; 14 moved them
    behind request.headers."""

    class _Request:
        headers = {"X-Forwarded-For": "203.0.113.9"}

    socket = _FakeSocket(peer="10.0.0.7")
    del socket.request_headers
    setattr(
        socket,
        attribute,
        {"X-Forwarded-For": "203.0.113.9"} if attribute == "request_headers" else _Request(),
    )

    guard = ConnectionGuard(trusted_proxies=["10.0.0.7"])

    assert guard.client_address(socket) == "203.0.113.9"
