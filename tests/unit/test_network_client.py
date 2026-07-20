"""Unit tests for NetworkClient's exponential-backoff reconnection loop.

Exercises `_connect_and_listen`/`_reconnect_loop` directly as coroutines
against a fake websocket connection — no real socket, thread, or Tkinter
involved, and `asyncio.sleep` is patched out so the tests run instantly
instead of waiting out real backoff delays.
"""

import asyncio
import json
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import websockets

from client import network_client as nc


class _FakeConnection:
    """Minimal async context manager / async iterator standing in for a
    `websockets` connection. Iterating it always raises immediately —
    `raise_on_iterate` controls whether that looks like a clean close
    (`StopAsyncIteration`, the default) or a drop (`ConnectionClosed`).
    `recv()` answers the one-shot auth handshake reply every fresh
    connection performs before its play/reconnect frame."""

    def __init__(
        self,
        raise_on_iterate: Optional[BaseException] = None,
        auth_reply: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.sent_payloads: List[str] = []
        self._raise_on_iterate = raise_on_iterate or StopAsyncIteration()
        self._auth_reply = auth_reply if auth_reply is not None else {"type": "auth", "status": "ok"}

    async def __aenter__(self) -> "_FakeConnection":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def send(self, payload: str) -> None:
        self.sent_payloads.append(payload)

    async def recv(self) -> str:
        return json.dumps(self._auth_reply)

    def __aiter__(self) -> "_FakeConnection":
        return self

    async def __anext__(self) -> Any:
        raise self._raise_on_iterate


def _statuses(messages: List[Dict[str, Any]]) -> List[str]:
    return [m["status"] for m in messages if m["type"] == nc.MSG_TYPE_CONNECTION_STATUS]


class TestInitialConnection(unittest.IsolatedAsyncioTestCase):
    async def test_initial_connect_failure_is_not_retried(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret")
        messages: List[Dict[str, Any]] = []
        client._on_message = messages.append

        def fake_connect(url: str):
            raise OSError("connection refused")

        with patch.object(nc.websockets, "connect", side_effect=fake_connect):
            await client._connect_and_listen()

        self.assertEqual(messages, [])

    async def test_sends_auth_then_play_frame_on_first_connect(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret")
        client._on_message = lambda m: None
        connection = _FakeConnection()

        with patch.object(nc.websockets, "connect", side_effect=lambda url: connection):
            await client._connect_and_listen()

        self.assertEqual(
            json.loads(connection.sent_payloads[0]),
            {"type": "auth", "action": "login", "username": "alice", "password": "secret"},
        )
        self.assertEqual(json.loads(connection.sent_payloads[1]), {"type": "play"})

    async def test_failed_reauth_on_first_connect_is_not_retried(self):
        client = nc.NetworkClient("ws://fake", "alice", "wrong-password")
        messages: List[Dict[str, Any]] = []
        client._on_message = messages.append
        connection = _FakeConnection(auth_reply={"type": "error", "message": "invalid credentials"})

        with patch.object(nc.websockets, "connect", side_effect=lambda url: connection):
            await client._connect_and_listen()

        # Only the auth frame went out — no play frame followed a failed handshake.
        self.assertEqual(len(connection.sent_payloads), 1)
        self.assertEqual(_statuses(messages), [])


class TestReconnectionBackoff(unittest.IsolatedAsyncioTestCase):
    async def test_recovers_from_a_drop_and_sends_reconnect_frame(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret")
        messages: List[Dict[str, Any]] = []
        client._on_message = messages.append

        first_conn = _FakeConnection(raise_on_iterate=websockets.ConnectionClosed(None, None))
        second_conn = _FakeConnection()  # listens cleanly, ending the task
        connect_calls: List[str] = []

        def fake_connect(url: str):
            connect_calls.append(url)
            return first_conn if len(connect_calls) == 1 else second_conn

        async def fake_sleep(seconds: float) -> None:
            return None

        with patch.object(nc.websockets, "connect", side_effect=fake_connect), \
                patch.object(nc.asyncio, "sleep", side_effect=fake_sleep):
            await client._connect_and_listen()

        self.assertEqual(len(connect_calls), 2)
        self.assertEqual(json.loads(first_conn.sent_payloads[1]), {"type": "play"})
        self.assertEqual(
            json.loads(second_conn.sent_payloads[0]),
            {"type": "auth", "action": "login", "username": "alice", "password": "secret"},
        )
        self.assertEqual(
            json.loads(second_conn.sent_payloads[1]),
            {"type": "reconnect", "username": "alice"},
        )
        self.assertEqual(
            _statuses(messages),
            [nc.STATUS_DISCONNECTED, nc.STATUS_RECONNECTING, nc.STATUS_CONNECTED],
        )

    async def test_backoff_delay_grows_and_caps_then_gives_up(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret")
        messages: List[Dict[str, Any]] = []
        client._on_message = messages.append
        delays: List[float] = []

        async def fake_sleep(seconds: float) -> None:
            delays.append(seconds)

        with patch.object(nc.asyncio, "sleep", side_effect=fake_sleep):
            while await client._reconnect_loop():
                pass

        self.assertEqual(delays, [1.0, 2.0, 4.0, 8.0, 8.0, 8.0])
        self.assertGreaterEqual(client._reconnect_elapsed_seconds, 30.0)
        statuses = _statuses(messages)
        self.assertEqual(statuses[-1], nc.STATUS_RECONNECT_FAILED)
        self.assertEqual(statuses[:-1], [nc.STATUS_RECONNECTING] * len(delays))

    async def test_reconnect_loop_stops_immediately_once_closing(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret")
        messages: List[Dict[str, Any]] = []
        client._on_message = messages.append
        client._closing = True

        recovered = await client._reconnect_loop()

        self.assertFalse(recovered)
        self.assertEqual(messages, [])

    async def test_successful_reconnect_resets_backoff_state(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret")
        client._on_message = lambda m: None
        client._reconnect_attempt = 4
        client._reconnect_elapsed_seconds = 23.0

        client._on_reconnected()

        self.assertEqual(client._reconnect_attempt, 0)
        self.assertEqual(client._reconnect_elapsed_seconds, 0.0)

    async def test_gives_up_after_exhausting_the_reconnect_window(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret")
        messages: List[Dict[str, Any]] = []
        client._on_message = messages.append

        first_conn = _FakeConnection(raise_on_iterate=websockets.ConnectionClosed(None, None))
        connect_calls: List[str] = []

        def fake_connect(url: str):
            connect_calls.append(url)
            if len(connect_calls) == 1:
                return first_conn
            raise OSError("still refusing")

        async def fake_sleep(seconds: float) -> None:
            return None

        with patch.object(nc.websockets, "connect", side_effect=fake_connect), \
                patch.object(nc.asyncio, "sleep", side_effect=fake_sleep):
            await client._connect_and_listen()

        self.assertEqual(_statuses(messages)[-1], nc.STATUS_RECONNECT_FAILED)
        self.assertGreater(len(connect_calls), 1)


class TestCancelSearch(unittest.IsolatedAsyncioTestCase):
    async def test_send_cancel_search_sends_frame_on_the_loop(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret")
        connection = _FakeConnection()
        client._loop = asyncio.get_running_loop()
        client._connection = connection

        client.send_cancel_search()
        # run_coroutine_threadsafe schedules via call_soon_threadsafe, which needs
        # a couple of loop turns to both create and advance the task even when
        # called from the loop's own thread, as this test does.
        for _ in range(5):
            await asyncio.sleep(0)

        self.assertEqual(json.loads(connection.sent_payloads[0]), {"type": "cancel_search"})

    async def test_send_cancel_search_without_a_running_loop_is_a_noop(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret")

        client.send_cancel_search()  # must not raise, even though nothing is connected


class TestRoomHandshakes(unittest.IsolatedAsyncioTestCase):
    async def test_create_room_handshake(self):
        client = nc.NetworkClient("ws://fake", "alice", "secret", initial_action="create_room")
        client._on_message = lambda m: None
        connection = _FakeConnection()

        with patch.object(nc.websockets, "connect", side_effect=lambda url: connection):
            await client._connect_and_listen()

        self.assertEqual(json.loads(connection.sent_payloads[1]), {"type": "create_room"})

    async def test_join_room_handshake_carries_the_room_id(self):
        client = nc.NetworkClient(
            "ws://fake", "alice", "secret", initial_action="join_room", room_id="ABC123"
        )
        client._on_message = lambda m: None
        connection = _FakeConnection()

        with patch.object(nc.websockets, "connect", side_effect=lambda url: connection):
            await client._connect_and_listen()

        self.assertEqual(
            json.loads(connection.sent_payloads[1]), {"type": "join_room", "room_id": "ABC123"}
        )


if __name__ == "__main__":
    unittest.main()
