"""Unit tests for HeartbeatMonitor."""

import pytest
import pytest_asyncio
from server.infrastructure.services.heartbeat import HeartbeatMonitor


class MockSession:
    def __init__(self, username: str):
        self.username = username
        self.sent_messages = []

    async def send(self, message):
        self.sent_messages.append(message)


@pytest.mark.asyncio
async def test_heartbeat_ping_and_timeout():
    clock = [100.0]

    def mock_time():
        return clock[0]

    disconnected = []

    def on_disc(session):
        disconnected.append(session)

    monitor = HeartbeatMonitor(
        ping_interval=5.0,
        pong_timeout=3.0,
        on_disconnect=on_disc,
        time_fn=mock_time,
    )

    s1 = MockSession("Alice")
    monitor.register_session(s1)

    # First ping iteration at t=101s (within interval+timeout)
    clock[0] = 101.0
    await monitor._ping_all()
    assert len(s1.sent_messages) == 1
    assert s1.sent_messages[0]["type"] == "ping"
    assert len(disconnected) == 0

    # Advance clock beyond timeout threshold (t=110s, diff 10s > 8s)
    clock[0] = 110.0
    await monitor._ping_all()
    assert len(disconnected) == 1
    assert disconnected[0] is s1
