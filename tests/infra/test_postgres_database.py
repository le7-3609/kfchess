"""PostgresDatabase behaviour against a real PostgreSQL.

Pins the properties the SQLite adapter already guarantees and the port had to
carry over intact: an all-or-nothing save, an idempotent one, statistics
recomputed rather than incremented, and an absolute ELO write that survives
replay.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from server.infrastructure.database.password_hashing import BCRYPT_COST_FACTOR, cost_of

pytestmark = pytest.mark.infra

_PASSWORD = "password123"


@dataclass
class FakeMove:
    move_number: int
    from_square: str
    to_square: str
    piece_type: str
    piece_color: str
    captured_piece: Optional[str]
    timestamp: float


@dataclass
class FakeGame:
    room_id: str
    white_player_id: int
    black_player_id: int
    winner_id: Optional[int]
    result: str
    white_elo_before: int
    white_elo_after: int
    black_elo_before: int
    black_elo_after: int
    started_at: datetime
    ended_at: datetime


def _game(room_id: str, white: int, black: int, winner: Optional[int]) -> FakeGame:
    started = datetime.now(timezone.utc) - timedelta(seconds=60)
    return FakeGame(
        room_id=room_id,
        white_player_id=white,
        black_player_id=black,
        winner_id=winner,
        result="checkmate",
        white_elo_before=1200,
        white_elo_after=1216,
        black_elo_before=1200,
        black_elo_after=1184,
        started_at=started,
        ended_at=started + timedelta(seconds=47),
    )


def _moves(count: int) -> List[FakeMove]:
    return [
        FakeMove(
            move_number=i,
            from_square="e2",
            to_square="e4",
            piece_type="P",
            piece_color="W" if i % 2 else "B",
            captured_piece=None,
            timestamp=float(i * 1000),
        )
        for i in range(1, count + 1)
    ]


async def _two_players(database):
    return (
        await database.create_user("Alice", _PASSWORD),
        await database.create_user("Bob", _PASSWORD),
    )


@pytest.mark.asyncio
async def test_a_user_round_trips_through_registration_and_login(postgres_database):
    user_id = await postgres_database.create_user("Alice", _PASSWORD)

    assert user_id is not None
    assert await postgres_database.authenticate_user("Alice", _PASSWORD) == (
        user_id, "Alice", 1200
    )
    assert await postgres_database.authenticate_user("Alice", "wrong") is None


@pytest.mark.asyncio
async def test_a_duplicate_username_is_refused_without_raising(postgres_database):
    await postgres_database.create_user("Alice", _PASSWORD)

    assert await postgres_database.create_user("Alice", "another") is None


@pytest.mark.asyncio
async def test_a_password_is_hashed_at_the_pinned_cost(postgres_database):
    await postgres_database.create_user("Alice", _PASSWORD)

    async with postgres_database._require_pool().acquire() as connection:
        stored = await connection.fetchval(
            "SELECT password_hash FROM users WHERE username = $1", "Alice"
        )

    assert stored != _PASSWORD
    assert cost_of(stored) == BCRYPT_COST_FACTOR


@pytest.mark.asyncio
async def test_ping_answers_while_the_pool_is_open(postgres_database):
    assert await postgres_database.ping() is True


@pytest.mark.asyncio
async def test_a_finished_game_saves_with_its_moves_and_statistics(postgres_database):
    white, black = await _two_players(postgres_database)

    outcome = await postgres_database.save_completed_game(
        _game("ROOM01", white, black, winner=white), _moves(6)
    )

    assert outcome is not None and outcome.already_existed is False
    assert len(await postgres_database.get_moves(outcome.game_id)) == 6
    assert await postgres_database.get_game_statistics(white) == (1, 1, 0, 0, 1216, 1216)
    assert await postgres_database.get_game_statistics(black) == (1, 0, 1, 0, 1184, 1184)


@pytest.mark.asyncio
async def test_saving_the_same_room_twice_is_a_no_op_not_a_failure(postgres_database):
    """`ON CONFLICT (room_id) DO NOTHING` is what makes at-least-once delivery
    survivable: a redelivered persistence event must resolve to the game already
    written, not to a second one and not to an error."""
    white, black = await _two_players(postgres_database)
    game = _game("ROOM02", white, black, winner=white)

    first = await postgres_database.save_completed_game(game, _moves(4))
    second = await postgres_database.save_completed_game(game, _moves(4))

    assert first.already_existed is False
    assert second.already_existed is True
    assert second.game_id == first.game_id
    assert len(await postgres_database.get_moves(first.game_id)) == 4, (
        "a redelivered save must not append a second copy of the move list"
    )


@pytest.mark.asyncio
async def test_a_save_with_a_bad_foreign_key_rolls_the_whole_batch_back(postgres_database):
    """Not a game row with no moves, and not stale statistics: all or nothing."""
    white, _ = await _two_players(postgres_database)
    missing_player = 999_999

    outcome = await postgres_database.save_completed_game(
        _game("ROOM03", white, missing_player, winner=white), _moves(3)
    )

    assert outcome is None
    async with postgres_database._require_pool().acquire() as connection:
        assert await connection.fetchval(
            "SELECT COUNT(*) FROM games WHERE room_id = $1", "ROOM03"
        ) == 0
        assert await connection.fetchval("SELECT COUNT(*) FROM moves") == 0


@pytest.mark.asyncio
async def test_statistics_are_recomputed_from_source_across_several_games(postgres_database):
    white, black = await _two_players(postgres_database)

    await postgres_database.save_completed_game(_game("R1", white, black, white), _moves(2))
    await postgres_database.save_completed_game(_game("R2", white, black, black), _moves(2))
    await postgres_database.save_completed_game(_game("R3", white, black, None), _moves(2))

    total, wins, losses, draws, _peak, _low = await postgres_database.get_game_statistics(white)
    assert (total, wins, losses, draws) == (3, 1, 1, 1)


@pytest.mark.asyncio
async def test_an_elo_write_is_absolute_and_therefore_replay_safe(postgres_database):
    await postgres_database.create_user("Alice", _PASSWORD)

    assert await postgres_database.update_elo("Alice", 1350) is True
    await postgres_database.update_elo("Alice", 1350)

    assert (await postgres_database.get_user_by_username("Alice"))[2] == 1350


@pytest.mark.asyncio
async def test_updating_an_unknown_user_reports_failure(postgres_database):
    assert await postgres_database.update_elo("Nobody", 1350) is False


@pytest.mark.asyncio
async def test_a_replay_reads_back_the_game_and_both_usernames(postgres_database):
    white, black = await _two_players(postgres_database)
    outcome = await postgres_database.save_completed_game(
        _game("ROOM04", white, black, winner=black), _moves(2)
    )

    row = await postgres_database.get_game(outcome.game_id)

    assert row[1] == "ROOM04"
    assert row[-2:] == ("Alice", "Bob")


@pytest.mark.asyncio
async def test_an_unknown_game_reads_as_none(postgres_database):
    assert await postgres_database.get_game(999_999) is None
    assert await postgres_database.get_moves(999_999) == []


@pytest.mark.asyncio
async def test_the_leaderboard_excludes_players_with_no_finished_game(postgres_database):
    white, black = await _two_players(postgres_database)
    await postgres_database.create_user("Newcomer", _PASSWORD)
    await postgres_database.save_completed_game(
        _game("ROOM05", white, black, winner=white), _moves(2)
    )
    await postgres_database.update_elo("Alice", 1216)
    await postgres_database.update_elo("Bob", 1184)

    board = await postgres_database.get_leaderboard(limit=10)

    assert [row[0] for row in board] == ["Alice", "Bob"]


@pytest.mark.asyncio
async def test_the_leaderboard_honours_its_limit(postgres_database):
    white, black = await _two_players(postgres_database)
    await postgres_database.save_completed_game(
        _game("ROOM06", white, black, winner=white), _moves(2)
    )

    assert len(await postgres_database.get_leaderboard(limit=1)) == 1
