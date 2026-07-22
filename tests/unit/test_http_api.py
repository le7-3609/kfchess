"""Unit tests for the read-only HTTP API endpoints."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from server.application.game_query_service import GameQueryService
from server.application.game_result import GameResult, PersistedMove
from server.infrastructure.database.database import Database
from server.presentation.http_api import HttpApi


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def client(temp_db):
    api = HttpApi(GameQueryService(temp_db))
    async with TestClient(TestServer(api.build_app())) as test_client:
        yield test_client


async def _seed_game(db):
    white_id = await db.create_user("white", "pw", initial_elo=1200)
    black_id = await db.create_user("black", "pw", initial_elo=1200)
    moves = [
        PersistedMove(1, "e2", "e4", "P", "white", None, 500.0),
        PersistedMove(2, "d7", "d5", "P", "black", "P", 900.0),
    ]
    result = GameResult(
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
        moves=moves,
    )
    return await db.save_completed_game(result, moves)


@pytest.mark.asyncio
async def test_get_game_returns_200_with_shape(client, temp_db):
    game_id = await _seed_game(temp_db)

    resp = await client.get(f"/api/games/{game_id}")
    assert resp.status == 200
    body = await resp.json()

    assert body["game"]["white_username"] == "white"
    assert body["game"]["result"] == "checkmate"
    assert len(body["moves"]) == 2
    assert body["moves"][0] == {
        "move_number": 1, "from": "e2", "to": "e4",
        "piece": "P", "color": "white", "captured_piece": None, "timestamp": 500.0,
    }
    assert body["moves"][1]["captured_piece"] == "P"


@pytest.mark.asyncio
async def test_get_game_unknown_returns_404(client):
    resp = await client.get("/api/games/999999")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_get_game_non_integer_returns_400(client):
    resp = await client.get("/api/games/abc")
    assert resp.status == 400


@pytest.mark.asyncio
async def test_get_game_pgn_returns_pgn_document(client, temp_db):
    game_id = await _seed_game(temp_db)

    resp = await client.get(f"/api/games/{game_id}/pgn")
    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("application/x-chess-pgn")
    assert "ROOM01.pgn" in resp.headers["Content-Disposition"]
    text = await resp.text()
    assert '[Variant "Kung Fu Chess"]' in text
    assert text.rstrip().endswith("1-0")


@pytest.mark.asyncio
async def test_get_game_pgn_unknown_returns_404(client):
    resp = await client.get("/api/games/999999/pgn")
    assert resp.status == 404


@pytest.mark.asyncio
async def test_leaderboard_returns_ranked_json(client, temp_db):
    await _seed_game(temp_db)
    await temp_db.update_elo("white", 1300)
    await temp_db.update_elo("black", 1100)

    resp = await client.get("/api/leaderboard")
    assert resp.status == 200
    board = await resp.json()
    assert [row["username"] for row in board] == ["white", "black"]
    assert board[0]["elo"] == 1300
    assert board[0]["wins"] == 1


@pytest.mark.asyncio
async def test_leaderboard_empty_when_no_games(client):
    resp = await client.get("/api/leaderboard")
    assert resp.status == 200
    assert await resp.json() == []
