"""Unit tests for DisconnectHandler."""

import asyncio
import pytest
import pytest_asyncio
from server.application.disconnect_handler import DisconnectHandler


class MockSession:
    def __init__(self, username: str, color: str):
        self.username = username
        self.color = color
        self.connected_flag = True
        self.sent_messages = []

    @property
    def connected(self):
        return self.connected_flag

    def disconnect(self):
        self.connected_flag = False

    def reconnect(self, ws):
        self.connected_flag = True

    async def send(self, message):
        self.sent_messages.append(message)


class MockRoom:
    def __init__(self, s1, s2):
        self.room_id = "test_room"
        self.white_player = s1
        self.black_player = s2
        self.service = None


@pytest.mark.asyncio
async def test_handle_disconnect_starts_countdown():
    s1 = MockSession("Alice", "w")
    s2 = MockSession("Bob", "b")
    room = MockRoom(s1, s2)

    handler = DisconnectHandler(game_room=room, timeout_seconds=1)
    handler.handle_disconnect(s1, s2)

    assert handler.is_disconnected(s1)
    assert not s1.connected

    # Wait for short 1s countdown loop to complete
    await asyncio.sleep(1.2)
    assert len(s2.sent_messages) > 0
    assert s2.sent_messages[-1]["type"] == "forfeit_victory"


@pytest.mark.asyncio
async def test_reconnect_cancels_countdown():
    s1 = MockSession("Alice", "w")
    s2 = MockSession("Bob", "b")
    room = MockRoom(s1, s2)

    handler = DisconnectHandler(game_room=room, timeout_seconds=5)
    handler.handle_disconnect(s1, s2)
    assert handler.is_disconnected(s1)

    reconnected = await handler.handle_reconnect(s1, new_websocket=object())
    assert reconnected is True
    assert s1.connected
    assert not handler.is_disconnected(s1)
