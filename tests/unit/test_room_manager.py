"""Unit tests for RoomManager."""

import asyncio

import pytest
from server.application.room_manager import RoomManager
from server.domain.room.game_room import RoomState
from server.domain.room.room_role import RoomRole
from shared.events import GameEndedEvent


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


def _seated_room(rm: RoomManager):
    """Fill both seats of a fresh room, which starts its game."""
    room_id = rm.create_room(MockSession("Alice"))
    rm.join_room(room_id, MockSession("Bob"))
    return room_id, rm.get_room(room_id)


def _track_stop(room):
    """Wrap room.stop() with a call counter, keeping the real teardown."""
    calls = []
    original_stop = room.stop

    async def counting_stop():
        calls.append(True)
        await original_stop()

    room.stop = counting_stop
    return calls


async def _wait_until_reaped(room, timeout: float = 2.0) -> None:
    """Wait for *room* to both schedule and finish its own teardown.

    Registry removal is not the completion signal: the reaper unindexes the
    room before it awaits stop(), so a test watching the dictionary would run
    on while the room is still draining its tick loop.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while room._expiry_task is None:
        if loop.time() > deadline:
            raise AssertionError(f"Room {room.room_id} never scheduled its expiry")
        await asyncio.sleep(0.01)
    await asyncio.wait_for(room._expiry_task, timeout=timeout)


def test_create_and_get_room():
    rm = RoomManager()
    s1 = MockSession("Alice")

    room_id = rm.create_room(s1)
    assert len(room_id) == 6

    room = rm.get_room(room_id)
    assert room is not None
    assert room.white_player is s1


def test_join_room():
    rm = RoomManager()
    s1 = MockSession("Alice")
    s2 = MockSession("Bob")
    v1 = MockSession("Viewer1")

    room_id = rm.create_room(s1)

    role2 = rm.join_room(room_id, s2)
    assert role2 == RoomRole.BLACK_PLAYER

    role3 = rm.join_room(room_id, v1)
    assert role3 == RoomRole.VIEWER


def test_list_rooms():
    rm = RoomManager()
    s1 = MockSession("Alice")
    r_id = rm.create_room(s1)

    summaries = rm.list_rooms()
    assert len(summaries) == 1
    assert summaries[0].room_id == r_id
    assert summaries[0].white_username == "Alice"


def test_join_room_rejects_unknown_id():
    rm = RoomManager()
    with pytest.raises(KeyError):
        rm.join_room("NOPE12", MockSession("Lost"))


def test_room_ids_are_matched_case_insensitively():
    """Room codes are shown uppercase, so a client echoing lowercase must resolve."""
    rm = RoomManager()
    room_id = rm.create_room(MockSession("Alice"))

    assert rm.get_room(room_id.lower()) is rm.get_room(room_id)
    assert rm.join_room(f"  {room_id.lower()} ", MockSession("Bob")) == RoomRole.BLACK_PLAYER


def test_participants_are_indexed_to_their_room():
    rm = RoomManager()
    creator, joiner, viewer = MockSession("A"), MockSession("B"), MockSession("C")

    room_id = rm.create_room(creator)
    rm.join_room(room_id, joiner)
    rm.join_room(room_id, viewer)

    room = rm.get_room(room_id)
    assert rm.find_room_by_session(creator) is room
    assert rm.find_room_by_session(joiner) is room
    assert rm.find_room_by_session(viewer) is room
    assert rm.find_room_by_session(MockSession("Stranger")) is None


def test_release_session_clears_only_that_participant():
    rm = RoomManager()
    creator, joiner = MockSession("A"), MockSession("B")
    room_id = rm.create_room(creator)
    rm.join_room(room_id, joiner)

    rm.release_session(creator)

    assert rm.find_room_by_session(creator) is None
    assert rm.find_room_by_session(joiner) is rm.get_room(room_id)


def test_removing_a_room_purges_its_index_entries():
    rm = RoomManager()
    creator = MockSession("A")
    room_id = rm.create_room(creator)

    assert rm.remove_room(room_id)
    assert rm.room_count == 0
    assert rm.find_room_by_session(creator) is None
    assert not rm.remove_room(room_id)


@pytest.mark.asyncio
async def test_find_room_by_username_only_matches_a_disconnected_seat():
    """Reconnect arrives on a fresh socket, so the seat is found by identity —
    but only while that seat is actually awaiting a return.

    Async because the disconnect countdown schedules a task, which needs a
    running loop.
    """
    rm = RoomManager()
    creator, joiner = MockSession("Alice"), MockSession("Bob")
    room_id = rm.create_room(creator)
    rm.join_room(room_id, joiner)
    room = rm.get_room(room_id)

    assert rm.find_room_by_username("Alice") is None

    room.handle_disconnect(creator)
    assert rm.find_room_by_username("Alice") is room
    assert rm.find_room_by_username("Nobody") is None

    room.disconnect_handler.cancel_all()


def test_rooms_are_built_with_the_managers_database():
    """Matchmade and named rooms must keep ELO persistence, not silently lose it."""
    database = object()
    rm = RoomManager(database=database)
    room_id = rm.create_room(MockSession("Alice"))

    assert rm.get_room(room_id)._database is database


def test_generated_room_ids_are_unique_and_well_formed():
    rm = RoomManager()
    room_ids = {rm.create_room(MockSession(f"P{i}")) for i in range(50)}

    assert len(room_ids) == 50
    # str.isupper() is False for an all-digit string (no cased characters at
    # all), and IDs drawn from ascii_uppercase + digits are legitimately
    # all-digit sometimes — r == r.upper() is the check that actually means
    # "no lowercase characters" for every possible generated id.
    assert all(len(r) == 6 and r.isalnum() and r == r.upper() for r in room_ids)


# --- Room reaping -----------------------------------------------------------
# A finished room that stays indexed keeps ticking forever, so these pin the
# path from "the game ended" to "the registry no longer holds it".


@pytest.mark.asyncio
async def test_game_end_reaps_the_room_and_stops_it():
    rm = RoomManager()
    room_id, room = _seated_room(rm)
    stop_calls = _track_stop(room)
    await room.start()

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
    await room._expiry_task

    assert stop_calls, "the reaper must tear the room down, not just unindex it"
    assert rm.get_room(room_id) is None
    assert room_id not in {info.room_id for info in rm.list_rooms()}
    assert rm.room_count == 0
    assert room.state == RoomState.FINISHED


@pytest.mark.asyncio
async def test_reaping_cancels_the_rooms_background_tasks():
    """The point of reaping is the tasks, not the dictionary entry."""
    rm = RoomManager()
    room_id, room = _seated_room(rm)
    await room.start()
    runner = room._runner
    assert runner.running

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))
    await room._expiry_task

    assert not runner.running
    assert room._runner is None


@pytest.mark.asyncio
async def test_room_ending_mid_tick_does_not_stall_its_own_tick_loop():
    """The end-of-game event fires inside the runner's task — the same task
    `stop()` awaits — so an inline reap would leave it awaiting itself."""
    rm = RoomManager()
    room_id, room = _seated_room(rm)

    bus = room._core.event_bus
    original_on_tick = room._runner._on_tick

    async def end_the_game_mid_tick() -> None:
        await original_on_tick()
        bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))

    room._runner._on_tick = end_the_game_mid_tick
    await room.start()

    await _wait_until_reaped(room)
    assert rm.get_room(room_id) is None
    assert room.state == RoomState.FINISHED


@pytest.mark.asyncio
async def test_a_room_is_reaped_only_once():
    """Several endings can land in one resolution pass, and the forfeit path
    announces its own on top — none of which may schedule a second teardown."""
    rm = RoomManager()
    room_id, room = _seated_room(rm)
    stop_calls = _track_stop(room)
    await room.start()

    bus = room._core.event_bus
    for _ in range(3):
        bus.publish(GameEndedEvent(at_ms=0, reason="king_captured", winner="w"))
    await room._expiry_task

    assert len(stop_calls) == 1


@pytest.mark.asyncio
async def test_disconnect_forfeit_reaps_the_room():
    """A forfeit ends the game by writing straight to GameState, publishing no
    event — so it must announce its expiry itself or leak the room."""
    rm = RoomManager()
    room_id = rm.create_room(MockSession("Alice"))
    rm.join_room(room_id, MockSession("Bob"))
    room = rm.get_room(room_id)
    room._disconnect_handler._timeout_seconds = 1
    await room.start()

    room.handle_disconnect(room.white_player)

    await _wait_until_reaped(room, timeout=3.0)
    assert rm.get_room(room_id) is None
    assert room.state == RoomState.FINISHED


@pytest.mark.asyncio
async def test_reaped_room_frees_its_participants_to_join_another():
    """The session index must be purged with the room, or every player who
    finished a game is permanently 'already seated'."""
    rm = RoomManager()
    room_id, room = _seated_room(rm)
    white = room.white_player
    await room.start()

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="stalemate", winner=None))
    await room._expiry_task

    assert rm.find_room_by_session(white) is None
    assert rm.find_room_by_username("Alice") is None


@pytest.mark.asyncio
async def test_reaping_an_already_removed_room_is_a_no_op():
    rm = RoomManager()
    room_id, room = _seated_room(rm)
    rm.remove_room(room_id)

    await rm._reap_room(room_id)

    assert rm.room_count == 0


def test_a_room_with_no_reaper_ignores_its_own_expiry():
    """GameRoom is constructed directly by tests and bots with no manager
    behind it; ending such a game must not fail for want of a callback."""
    from server.application.game_room import GameRoom

    room = GameRoom(room_id="SOLO01")
    room.add_player(MockSession("Alice"))
    room.add_player(MockSession("Bob"))

    room._core.event_bus.publish(GameEndedEvent(at_ms=0, reason="checkmate", winner="w"))

    assert room._expiry_task is None
