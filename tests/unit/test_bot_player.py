"""Unit tests for the bot fallback: driver cadence, seating, and wiring."""

import asyncio
import pytest

from server.application.game_room import GameRoom
from server.domain.player.player_interface import DEFAULT_BOT_USERNAME, BotPlayerAdapter
from server.domain.room.game_room import RoomState
from server.infrastructure.services.bot_driver import BotDriver

_FAST_INTERVAL = 0.01


class FakeInputSource:
    """Stands in for RandomBotInputSource, recording how often it is polled."""

    def __init__(self, commands=None, raises: bool = False):
        self._commands = commands if commands is not None else ["cmd-a", "cmd-b"]
        self._raises = raises
        self.poll_count = 0

    def get_next_commands(self):
        self.poll_count += 1
        if self._raises:
            raise RuntimeError("bot exploded")
        return list(self._commands)


class MockSession:
    def __init__(self, username: str, user_id: int = 1, elo: int = 1200):
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

    async def send(self, message):
        self.sent_messages.append(message)


class MockDatabase:
    def __init__(self):
        self.elo_updates = {}

    async def update_elo(self, username: str, new_elo: int) -> bool:
        self.elo_updates[username] = new_elo
        return True


def _driver(source, submitted, is_game_over=lambda: False) -> BotDriver:
    return BotDriver(
        input_source=source,
        submit_command=submitted.append,
        is_game_over=is_game_over,
        move_interval_seconds=_FAST_INTERVAL,
    )


@pytest.mark.asyncio
async def test_driver_submits_the_input_source_commands():
    source, submitted = FakeInputSource(), []
    driver = _driver(source, submitted)

    await driver.start()
    assert driver.running
    await asyncio.sleep(_FAST_INTERVAL * 6)
    await driver.stop()

    assert not driver.running
    assert submitted[:2] == ["cmd-a", "cmd-b"]


@pytest.mark.asyncio
async def test_driver_stops_polling_once_the_game_is_over():
    source, submitted = FakeInputSource(), []
    driver = _driver(source, submitted, is_game_over=lambda: True)

    await driver.start()
    await asyncio.sleep(_FAST_INTERVAL * 5)

    assert submitted == []
    assert source.poll_count == 0
    await driver.stop()


@pytest.mark.asyncio
async def test_driver_survives_a_failing_input_source():
    """A throwing bot must not kill the driver task and freeze the game."""
    source, submitted = FakeInputSource(raises=True), []
    driver = _driver(source, submitted)

    await driver.start()
    await asyncio.sleep(_FAST_INTERVAL * 5)
    still_running = driver.running
    await driver.stop()

    assert still_running
    assert source.poll_count > 1
    assert submitted == []


@pytest.mark.asyncio
async def test_stop_is_idempotent_without_start():
    driver = _driver(FakeInputSource(), [])
    await driver.stop()
    assert not driver.running


def test_driver_rejects_a_non_positive_interval():
    with pytest.raises(ValueError):
        BotDriver(FakeInputSource(), lambda c: None, lambda: False, move_interval_seconds=0)


def test_bot_adapter_satisfies_the_seat_contract():
    bot = BotPlayerAdapter()

    bot.assign_color("b")
    assert bot.color == "b"
    assert bot.username == DEFAULT_BOT_USERNAME
    assert bot.is_bot
    assert bot.connected

    # A bot has no socket, so disconnecting must not unseat it.
    bot.disconnect()
    assert bot.connected


def test_add_bot_opponent_seats_and_wires_the_bot():
    room = GameRoom(room_id="BOT001")
    room.add_player(MockSession("Human"))

    bot = room.add_bot_opponent(move_interval_seconds=_FAST_INTERVAL)

    assert room.is_full
    assert room.state == RoomState.PLAYING
    assert room.has_bot
    assert room.black_player is bot
    assert bot.color == "b"


def test_seated_bot_reads_the_rooms_own_engine_core():
    """The bot must be built against the room's repositories, not a fresh core.

    A bot wired to a different core would see an empty board and never produce
    a command — the exact failure mode that leaves a seated bot frozen.
    """
    room = GameRoom(room_id="BOT002")
    room.add_player(MockSession("Human"))
    bot = room.add_bot_opponent(move_interval_seconds=_FAST_INTERVAL)

    commands = bot.input_source.get_next_commands()

    assert commands, "bot produced no move against the room's live starting position"


@pytest.mark.asyncio
async def test_starting_a_bot_room_runs_the_driver():
    room = GameRoom(room_id="BOT003")
    room.add_player(MockSession("Human"))
    room.add_bot_opponent(move_interval_seconds=_FAST_INTERVAL)

    await room.start()
    driver = room._bot_driver
    assert driver.running

    await room.stop()
    assert not driver.running
    # Stopping releases the driver too, so a reaped room stops pinning the bot
    # and the engine core it was wired against.
    assert room._bot_driver is None
    assert room.state == RoomState.FINISHED


def test_add_bot_opponent_requires_exactly_one_seated_player():
    empty_room = GameRoom(room_id="BOT004")
    with pytest.raises(ValueError):
        empty_room.add_bot_opponent()

    full_room = GameRoom(room_id="BOT005")
    full_room.add_player(MockSession("White"))
    full_room.add_player(MockSession("Black"))
    with pytest.raises(ValueError):
        full_room.add_bot_opponent()


@pytest.mark.asyncio
async def test_forfeit_against_a_bot_does_not_touch_stored_ratings():
    """A bot holds no account row, so a game against it must stay unrated."""
    database = MockDatabase()
    room = GameRoom(room_id="BOT006", database=database)
    human = MockSession("Human")
    room.add_player(human)
    bot = room.add_bot_opponent(move_interval_seconds=_FAST_INTERVAL)

    await room._apply_forfeit(human, bot)

    assert database.elo_updates == {}
