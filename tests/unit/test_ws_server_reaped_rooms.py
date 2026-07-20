"""Unit tests for how the WebSocket server routes frames aimed at a reaped room.

A room can be reaped between a client deciding to send a frame and the server
reading it, so every lobby and game handler must answer a vanished room with a
structured `error` message rather than raising out of the connection loop.

These drive the handlers directly instead of over real sockets: the contract
under test is the routing decision, and a live server adds a matchmaking loop
and 20Hz broadcasts that only obscure it.
"""

import asyncio

import pytest

from server.application.dtos.network_frames import MSG_ERROR
from server.application.room_manager import RoomManager
from server.domain.room.game_room import RoomState
from server.presentation.ws_server import KFChessServer
from shared.events import GameEndedEvent


class MockSession:
    """A session whose sends are recorded rather than written to a socket."""

    def __init__(self, username: str, send_error: Exception = None):
        self.username = username
        self.user_id = 1
        self.elo = 1200
        self.color = None
        self.connected = True
        self.sent_messages = []
        self._send_error = send_error

    def assign_color(self, color: str):
        self.color = color

    def disconnect(self):
        self.connected = False

    async def send(self, msg):
        if self._send_error is not None:
            raise self._send_error
        self.sent_messages.append(msg)


def _errors(session) -> list:
    return [m for m in session.sent_messages if m.get("type") == MSG_ERROR]


async def _reaped_room(rm: RoomManager, white, black):
    """Seat both players, run the game to a reaped end, return its room id."""
    room_id = rm.create_room(white)
    rm.join_room(room_id, black)
    room = rm.get_room(room_id)
    await room.start()

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
    await room._expiry_task
    return room_id


@pytest.mark.asyncio
async def test_late_move_into_a_reaped_room_answers_with_an_error():
    rm = RoomManager()
    server = KFChessServer(room_manager=rm)
    white, black = MockSession("Alice"), MockSession("Bob")
    await _reaped_room(rm, white, black)

    await server._handle_move(white, {"type": "move", "from": "e2", "to": "e4"})

    assert _errors(white), "a move into a vanished room must be answered, not dropped"


@pytest.mark.asyncio
async def test_joining_a_reaped_room_reports_that_it_no_longer_exists():
    rm = RoomManager()
    server = KFChessServer(room_manager=rm)
    white, black = MockSession("Alice"), MockSession("Bob")
    room_id = await _reaped_room(rm, white, black)

    latecomer = MockSession("Carol")
    await server._handle_join_room(latecomer, {"type": "join_room", "room_id": room_id})

    errors = _errors(latecomer)
    assert errors and room_id in errors[-1]["message"]
    assert "does not exist" in errors[-1]["message"]


@pytest.mark.asyncio
async def test_a_reaped_rooms_players_can_start_a_new_game():
    """Reaping must release the session index, or a finished player is stuck
    'already seated' in a room that no longer exists."""
    rm = RoomManager()
    server = KFChessServer(room_manager=rm)
    white, black = MockSession("Alice"), MockSession("Bob")
    await _reaped_room(rm, white, black)

    await server._handle_create_room(white)

    assert not _errors(white)
    assert rm.room_count == 1


@pytest.mark.asyncio
async def test_a_socket_dropping_mid_reap_does_not_break_the_teardown():
    """The final broadcast races the client's own close, so a send that raises
    must not abandon the reap and strand the room's tick loop."""
    rm = RoomManager()
    dropped = MockSession("Alice", send_error=ConnectionResetError("socket closed"))
    room_id = rm.create_room(dropped)
    rm.join_room(room_id, MockSession("Bob"))
    room = rm.get_room(room_id)
    await room.start()

    # Let a tick broadcast into the dead socket before the game ends on it.
    await asyncio.sleep(room._runner.tick_interval * 2)
    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
    await room._expiry_task

    assert rm.get_room(room_id) is None
    assert room.state == RoomState.FINISHED


@pytest.mark.asyncio
async def test_server_shutdown_tolerates_an_already_reaped_room():
    """Both teardown paths can fire on one room, and neither may raise."""
    rm = RoomManager()
    server = KFChessServer(room_manager=rm)
    white, black = MockSession("Alice"), MockSession("Bob")
    await _reaped_room(rm, white, black)

    await server.stop()

    assert rm.room_count == 0
