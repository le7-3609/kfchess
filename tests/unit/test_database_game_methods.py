"""Unit tests for the Database completed-game persistence methods."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from server.application.game_result import GameResult, PersistedMove
from server.infrastructure.database.database import Database


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def two_players(temp_db):
    """Two real users so game foreign keys resolve; returns their ids."""
    white_id = await temp_db.create_user("white", "pw", initial_elo=1200)
    black_id = await temp_db.create_user("black", "pw", initial_elo=1200)
    return white_id, black_id


def _game_result(
    white_id,
    black_id,
    *,
    room_id="ROOM01",
    winner_id=None,
    result="checkmate",
    white_after=1216,
    black_after=1184,
    moves=None,
):
    return GameResult(
        room_id=room_id,
        white_player_id=white_id,
        black_player_id=black_id,
        winner_id=winner_id,
        result=result,
        white_elo_before=1200,
        white_elo_after=white_after,
        black_elo_before=1200,
        black_elo_after=black_after,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        moves=moves or [],
    )


@pytest.mark.asyncio
async def test_save_game_inserts_game_moves_and_stats(temp_db, two_players):
    white_id, black_id = two_players
    moves = [
        PersistedMove(1, "e2", "e4", "P", "white", None, 500.0),
        PersistedMove(2, "e7", "e5", "P", "black", None, 900.0),
    ]
    game_result = _game_result(white_id, black_id, winner_id=white_id, moves=moves)

    game_id = await temp_db.save_completed_game(game_result, moves)

    assert game_id is not None
    conn = temp_db._require_connection()
    async with conn.execute("SELECT room_id, result, winner_id FROM games WHERE id = ?", (game_id,)) as cur:
        assert await cur.fetchone() == ("ROOM01", "checkmate", white_id)
    async with conn.execute("SELECT COUNT(*) FROM moves WHERE game_id = ?", (game_id,)) as cur:
        assert (await cur.fetchone())[0] == 2

    white_stats = await temp_db.get_game_statistics(white_id)
    black_stats = await temp_db.get_game_statistics(black_id)
    # (total, wins, losses, draws, peak, low)
    assert white_stats == (1, 1, 0, 0, 1216, 1216)
    assert black_stats == (1, 0, 1, 0, 1184, 1184)


@pytest.mark.asyncio
async def test_draw_counts_for_both_players(temp_db, two_players):
    white_id, black_id = two_players
    game_result = _game_result(
        white_id, black_id, winner_id=None, result="stalemate", white_after=1204, black_after=1196
    )

    await temp_db.save_completed_game(game_result, [])

    assert (await temp_db.get_game_statistics(white_id))[:4] == (1, 0, 0, 1)
    assert (await temp_db.get_game_statistics(black_id))[:4] == (1, 0, 0, 1)


@pytest.mark.asyncio
async def test_statistics_aggregate_across_games(temp_db, two_players):
    white_id, black_id = two_players
    # White wins, white wins, black wins → white: 2 wins / 1 loss.
    await temp_db.save_completed_game(
        _game_result(white_id, black_id, room_id="G1", winner_id=white_id, white_after=1216), []
    )
    await temp_db.save_completed_game(
        _game_result(white_id, black_id, room_id="G2", winner_id=white_id, white_after=1230), []
    )
    await temp_db.save_completed_game(
        _game_result(white_id, black_id, room_id="G3", winner_id=black_id, white_after=1210), []
    )

    total, wins, losses, draws, peak, low = await temp_db.get_game_statistics(white_id)
    assert (total, wins, losses, draws) == (3, 2, 1, 0)
    assert peak == 1230
    assert low == 1210


@pytest.mark.asyncio
async def test_duplicate_room_id_rolls_back(temp_db, two_players):
    white_id, black_id = two_players
    first = _game_result(white_id, black_id, room_id="DUP", winner_id=white_id)
    assert await temp_db.save_completed_game(first, []) is not None

    # Same room_id violates the UNIQUE constraint; the whole batch must roll back.
    dup_moves = [PersistedMove(1, "e2", "e4", "P", "white", None, 100.0)]
    assert await temp_db.save_completed_game(first, dup_moves) is None

    conn = temp_db._require_connection()
    async with conn.execute("SELECT COUNT(*) FROM games") as cur:
        assert (await cur.fetchone())[0] == 1
    async with conn.execute("SELECT COUNT(*) FROM moves") as cur:
        assert (await cur.fetchone())[0] == 0  # the duplicate's move never landed


@pytest.mark.asyncio
async def test_bad_foreign_key_rolls_back(temp_db, two_players):
    white_id, _ = two_players
    ghost_player = 999999
    game_result = _game_result(white_id, ghost_player, winner_id=white_id)

    assert await temp_db.save_completed_game(game_result, []) is None

    conn = temp_db._require_connection()
    async with conn.execute("SELECT COUNT(*) FROM games") as cur:
        assert (await cur.fetchone())[0] == 0
    assert await temp_db.get_game_statistics(white_id) is None


@pytest.mark.asyncio
async def test_statistics_absent_until_a_game_is_saved(temp_db, two_players):
    white_id, _ = two_players
    assert await temp_db.get_game_statistics(white_id) is None


@pytest.mark.asyncio
async def test_get_game_returns_row_with_usernames(temp_db, two_players):
    white_id, black_id = two_players
    game_result = _game_result(white_id, black_id, winner_id=white_id)
    game_id = await temp_db.save_completed_game(game_result, [])

    row = await temp_db.get_game(game_id)
    assert row is not None
    # (id, room_id, white_id, black_id, winner_id, result, elos..., started, ended, white_name, black_name)
    assert row[0] == game_id
    assert row[1] == "ROOM01"
    assert row[4] == white_id
    assert row[5] == "checkmate"
    assert row[-2] == "white"  # white_username
    assert row[-1] == "black"  # black_username


@pytest.mark.asyncio
async def test_get_game_unknown_id_returns_none(temp_db, two_players):
    assert await temp_db.get_game(424242) is None


@pytest.mark.asyncio
async def test_get_moves_ordered_by_move_number(temp_db, two_players):
    white_id, black_id = two_players
    moves = [
        PersistedMove(2, "e7", "e5", "P", "black", None, 900.0),
        PersistedMove(1, "e2", "e4", "P", "white", None, 500.0),
        PersistedMove(3, "g1", "f3", "N", "white", "p", 1400.0),
    ]
    game_result = _game_result(white_id, black_id, winner_id=white_id, moves=moves)
    game_id = await temp_db.save_completed_game(game_result, moves)

    rows = await temp_db.get_moves(game_id)
    assert [r[0] for r in rows] == [1, 2, 3]
    assert rows[0][1:3] == ("e2", "e4")
    assert rows[2][5] == "p"  # captured_piece round-trips


@pytest.mark.asyncio
async def test_leaderboard_orders_by_elo_and_excludes_gameless_users(temp_db, two_players):
    white_id, black_id = two_players
    # A third user who never finishes a game must not appear.
    await temp_db.create_user("idle", "pw", initial_elo=2000)
    await temp_db.save_completed_game(
        _game_result(white_id, black_id, winner_id=white_id, white_after=1250, black_after=1150), []
    )
    await temp_db.update_elo("white", 1250)
    await temp_db.update_elo("black", 1150)

    board = await temp_db.get_leaderboard()
    names = [row[0] for row in board]
    assert names == ["white", "black"]  # idle excluded, ordered by elo desc
    assert board[0][1] == 1250


@pytest.mark.asyncio
async def test_leaderboard_respects_limit(temp_db, two_players):
    white_id, black_id = two_players
    await temp_db.save_completed_game(
        _game_result(white_id, black_id, winner_id=white_id), []
    )
    assert len(await temp_db.get_leaderboard(limit=1)) == 1
