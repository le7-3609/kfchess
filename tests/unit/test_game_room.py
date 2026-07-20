"""Unit tests for GameRoom lifecycle and role assignment."""

import asyncio
import pytest
from unittest.mock import MagicMock
from server.application.game_room import GameRoom
from server.domain.room.game_room import RoomState
from server.domain.room.room_role import RoomRole
from server.presentation.ws_connection import PlayerSession
from shared.events import GameEndedEvent


class MockSession:
    def __init__(self, username: str, user_id: int, elo: int = 1200):
        self.username = username
        self.user_id = user_id
        self.elo = elo
        self._color = None
        self.sent_messages = []
        self.connected = True

    def assign_color(self, color: str):
        self._color = color

    @property
    def color(self):
        return self._color

    def disconnect(self):
        self.connected = False

    def reconnect(self, websocket):
        self.connected = True

    async def send(self, message):
        self.sent_messages.append(message)


class MockDatabase:
    def __init__(self):
        self.elo_updates = {}

    async def update_elo(self, username: str, new_elo: int) -> bool:
        self.elo_updates[username] = new_elo
        return True


def _seated_room(**kwargs):
    """Build a room with both seats filled (which starts the game)."""
    room = GameRoom(room_id="test_room", **kwargs)
    white = MockSession("Alice", 1)
    black = MockSession("Bob", 2)
    room.add_player(white)
    room.add_player(black)
    return room, white, black


def test_room_initial_state():
    room = GameRoom(room_id="test_room")
    assert room.room_id == "test_room"
    assert room.state == RoomState.WAITING
    assert not room.is_full


def test_add_players_assignment():
    room = GameRoom(room_id="test_room")
    s1 = MockSession("Alice", 1)
    s2 = MockSession("Bob", 2)

    role1 = room.add_player(s1)
    assert role1 == RoomRole.WHITE_PLAYER
    assert s1.color == "w"
    assert not room.is_full
    assert room.state == RoomState.WAITING

    role2 = room.add_player(s2)
    assert role2 == RoomRole.BLACK_PLAYER
    assert s2.color == "b"
    assert room.is_full
    assert room.state == RoomState.PLAYING


def test_add_third_player_raises_error():
    room = GameRoom(room_id="test_room")
    s1 = MockSession("Alice", 1)
    s2 = MockSession("Bob", 2)
    s3 = MockSession("Charlie", 3)

    room.add_player(s1)
    room.add_player(s2)

    with pytest.raises(ValueError, match="full"):
        room.add_player(s3)


def test_add_viewer():
    room = GameRoom(room_id="test_room")
    s1 = MockSession("Alice", 1)
    s2 = MockSession("Bob", 2)
    v1 = MockSession("Viewer1", 3)

    room.add_player(s1)
    room.add_player(s2)
    room.add_viewer(v1)

    assert room.role_of(v1) == RoomRole.VIEWER


@pytest.mark.asyncio
async def test_disconnect_preserves_seat_during_active_game():
    room, white, black = _seated_room()

    assert room.handle_disconnect(white) is True
    # Seat is held for the countdown window rather than freed.
    assert room.white_player is white
    assert room.disconnect_handler.is_disconnected(white)

    room.disconnect_handler.cancel_all()


@pytest.mark.asyncio
async def test_disconnect_of_viewer_is_not_preserved():
    room, white, black = _seated_room()
    viewer = MockSession("Viewer1", 3)
    room.add_viewer(viewer)

    # Viewers have no seat to hold, so the caller falls back to plain removal.
    assert room.handle_disconnect(viewer) is False
    assert not room.disconnect_handler.is_disconnected(viewer)


def test_disconnect_before_game_starts_is_not_preserved():
    room = GameRoom(room_id="test_room")
    white = MockSession("Alice", 1)
    room.add_player(white)

    assert room.state == RoomState.WAITING
    assert room.handle_disconnect(white) is False


@pytest.mark.asyncio
async def test_reconnect_rebinds_seat_and_cancels_countdown():
    room, white, black = _seated_room()
    room.handle_disconnect(white)

    new_ws = object()
    restored = await room.handle_reconnect("Alice", new_ws)

    assert restored is white
    assert room.white_player is white
    assert not room.disconnect_handler.is_disconnected(white)
    assert any(m["type"] == "opponent_reconnected" for m in black.sent_messages)


@pytest.mark.asyncio
async def test_reconnect_rejects_unknown_username():
    room, white, black = _seated_room()
    room.handle_disconnect(white)

    assert await room.handle_reconnect("Nobody", object()) is None

    room.disconnect_handler.cancel_all()


@pytest.mark.asyncio
async def test_reconnect_rejects_player_who_never_disconnected():
    room, white, black = _seated_room()

    assert await room.handle_reconnect("Alice", object()) is None


@pytest.mark.asyncio
async def test_countdown_expiry_forfeits_and_updates_elo():
    db = MockDatabase()
    room, white, black = _seated_room(database=db, disconnect_timeout_seconds=1)

    room.handle_disconnect(white)
    await asyncio.sleep(1.3)

    assert any(m["type"] == "forfeit_victory" for m in black.sent_messages)
    # Surviving player gains rating; the forfeiting player loses it.
    assert db.elo_updates["Bob"] > 1200
    assert db.elo_updates["Alice"] < 1200
    assert room.state == RoomState.FINISHED
    assert room.white_player is None


@pytest.mark.asyncio
async def test_natural_checkmate_updates_elo_in_database_and_sessions():
    db = MockDatabase()
    room, white, black = _seated_room(database=db)

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
    await room._elo_settlement_task

    assert db.elo_updates["Alice"] > 1200
    assert db.elo_updates["Bob"] < 1200
    assert white.elo == db.elo_updates["Alice"]
    assert black.elo == db.elo_updates["Bob"]


@pytest.mark.asyncio
async def test_natural_king_capture_rates_the_black_winner():
    db = MockDatabase()
    room, white, black = _seated_room(database=db)

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="king_captured", winner="b"))
    await room._elo_settlement_task

    assert db.elo_updates["Bob"] > 1200
    assert db.elo_updates["Alice"] < 1200


@pytest.mark.asyncio
async def test_natural_draw_updates_elo_for_both_players():
    db = MockDatabase()
    room, white, black = _seated_room(database=db)
    white.elo = 1400

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="stalemate", winner=None))
    await room._elo_settlement_task

    assert db.elo_updates["Alice"] < 1400
    assert db.elo_updates["Bob"] > 1200
    assert white.elo == db.elo_updates["Alice"]
    assert black.elo == db.elo_updates["Bob"]


@pytest.mark.asyncio
async def test_natural_game_end_against_a_bot_does_not_touch_elo():
    db = MockDatabase()
    room = GameRoom(room_id="bot_room", database=db)
    human = MockSession("Alice", 1)
    room.add_player(human)
    room.add_bot_opponent()

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
    await room._elo_settlement_task

    assert db.elo_updates == {}
    assert human.elo == 1200


@pytest.mark.asyncio
async def test_natural_game_end_without_a_database_does_not_raise():
    room, white, black = _seated_room()

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
    await room._elo_settlement_task

    assert white.elo == 1200
    assert black.elo == 1200


@pytest.mark.asyncio
async def test_natural_game_end_settles_elo_and_reaps_the_room_via_room_manager():
    """End-to-end through the real production wiring: RoomManager is what
    actually supplies on_room_expired, so this is the only place that
    exercises ELO settlement and reaping together."""
    from server.application.room_manager import RoomManager

    db = MockDatabase()
    rm = RoomManager(database=db)
    white, black = MockSession("Alice", 1), MockSession("Bob", 2)
    room_id = rm.create_room(white)
    rm.join_room(room_id, black)
    room = rm.get_room(room_id)

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
    await room._expiry_task
    await room._elo_settlement_task

    assert db.elo_updates["Alice"] > 1200
    assert db.elo_updates["Bob"] < 1200
    assert white.elo == db.elo_updates["Alice"]
    assert black.elo == db.elo_updates["Bob"]
    assert rm.get_room(room_id) is None
    assert room.state == RoomState.FINISHED
    assert room.state == RoomState.FINISHED


@pytest.mark.asyncio
async def test_handle_move_accepts_a_seated_players_own_piece():
    room, white, black = _seated_room()

    result = await room.handle_move(white, "e2", "e4")

    assert result.is_ok


@pytest.mark.asyncio
async def test_handle_move_rejects_a_spectator():
    """Viewers receive broadcasts but must not be able to influence the game."""
    room, white, black = _seated_room()
    viewer = MockSession("Watcher", 3)
    room.add_viewer(viewer)

    result = await room.handle_move(viewer, "e2", "e4")

    assert not result.is_ok
    assert "spectator" in result.error.lower()


@pytest.mark.asyncio
async def test_handle_move_rejects_an_opponents_piece():
    room, white, black = _seated_room()

    result = await room.handle_move(black, "e2", "e4")

    assert not result.is_ok


@pytest.mark.asyncio
async def test_handle_move_reports_a_malformed_square_instead_of_raising():
    """A bad square must come back as an error, not tear down the connection."""
    room, white, black = _seated_room()

    result = await room.handle_move(white, "zz", "e4")

    assert not result.is_ok


@pytest.mark.asyncio
async def test_handle_move_rejected_before_the_game_starts():
    room = GameRoom(room_id="waiting_room")
    lone_player = MockSession("Alone", 1)
    room.add_player(lone_player)

    result = await room.handle_move(lone_player, "e2", "e4")

    assert not result.is_ok


def test_role_and_opponent_lookup():
    room, white, black = _seated_room()
    viewer = MockSession("Watcher", 3)
    room.add_viewer(viewer)

    assert room.role_of(white) == RoomRole.WHITE_PLAYER
    assert room.role_of(black) == RoomRole.BLACK_PLAYER
    assert room.role_of(viewer) == RoomRole.VIEWER
    assert room.role_of(MockSession("Stranger", 9)) is None

    assert room.opponent_of(white) is black
    assert room.opponent_of(black) is white
    assert room.opponent_of(viewer) is None
    assert room.viewer_count == 1
