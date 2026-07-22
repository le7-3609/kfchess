"""Unit tests for GameQueryService DTO mapping and not-found handling."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from server.application.game_query_service import GameQueryService, LeaderboardRow
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
    white_id = await temp_db.create_user("white", "pw", initial_elo=1200)
    black_id = await temp_db.create_user("black", "pw", initial_elo=1200)
    return white_id, black_id


def _game_result(white_id, black_id, *, moves=None):
    return GameResult(
        room_id="ROOM01",
        white_player_id=white_id,
        black_player_id=black_id,
        winner_id=white_id,
        result="checkmate",
        white_elo_before=1200,
        white_elo_after=1216,
        black_elo_before=1200,
        black_elo_after=1184,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        moves=moves or [],
    )


@pytest.mark.asyncio
async def test_get_replay_maps_game_and_moves(temp_db, two_players):
    white_id, black_id = two_players
    moves = [
        PersistedMove(1, "e2", "e4", "P", "white", None, 500.0),
        PersistedMove(2, "d7", "d5", "P", "black", "P", 900.0),
    ]
    game_id = await temp_db.save_completed_game(_game_result(white_id, black_id, moves=moves), moves)

    service = GameQueryService(temp_db)
    replay = await service.get_replay(game_id)

    assert replay is not None
    assert replay.game_id == game_id
    assert replay.white_username == "white"
    assert replay.black_username == "black"
    assert replay.winner_id == white_id
    assert replay.result == "checkmate"
    assert replay.white_elo_before == 1200 and replay.white_elo_after == 1216
    assert [m.move_number for m in replay.moves] == [1, 2]
    assert replay.moves[1].captured_piece == "P"
    assert replay.moves[0].timestamp == 500.0


@pytest.mark.asyncio
async def test_get_replay_unknown_id_returns_none(temp_db, two_players):
    service = GameQueryService(temp_db)
    assert await service.get_replay(999) is None


@pytest.mark.asyncio
async def test_get_leaderboard_returns_dtos_in_order(temp_db, two_players):
    white_id, black_id = two_players
    await temp_db.save_completed_game(_game_result(white_id, black_id), [])
    await temp_db.update_elo("white", 1300)
    await temp_db.update_elo("black", 1100)

    service = GameQueryService(temp_db)
    board = await service.get_leaderboard()

    assert all(isinstance(row, LeaderboardRow) for row in board)
    assert [row.username for row in board] == ["white", "black"]
    assert board[0].elo == 1300
    assert board[0].wins == 1
