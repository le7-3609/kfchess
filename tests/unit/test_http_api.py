"""Unit tests for the read-only HTTP API endpoints."""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from server.application.auth_service import AuthService
from server.application.game_query_service import GameQueryService
from server.application.game_result import GameResult, PersistedMove
from server.application.health import ReadinessProbe
from server.application.token_service import TokenService
from server.infrastructure.database.database import MAX_LEADERBOARD_LIMIT, Database
from server.infrastructure.observability.metrics import MetricsRegistry
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
    return (await db.save_completed_game(result, moves)).game_id


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


# ---------------------------------------------------------------------------
# Operational endpoints
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def probed_client(temp_db):
    """A client whose API carries a readiness probe wired to the real database."""
    probe = ReadinessProbe()
    probe.register("database", temp_db.ping)
    api = HttpApi(GameQueryService(temp_db), readiness=probe)
    async with TestClient(TestServer(api.build_app())) as test_client:
        yield test_client, probe


@pytest.mark.asyncio
async def test_healthz_answers_without_touching_the_database(temp_db):
    """Liveness must stay local. A liveness probe that reads the database turns
    one slow database into a fleet-wide restart storm, because every replica
    fails at once and the restarts hit the struggling database harder."""
    await temp_db.close()
    api = HttpApi(GameQueryService(temp_db))

    async with TestClient(TestServer(api.build_app())) as client:
        resp = await client.get("/healthz")
        body = await resp.json()

    assert resp.status == 200
    assert body["status"] == "alive"


@pytest.mark.asyncio
async def test_readyz_reports_ready_while_the_database_answers(probed_client):
    client, _ = probed_client

    resp = await client.get("/readyz")

    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["database"] is True


@pytest.mark.asyncio
async def test_readyz_reports_503_when_the_database_is_gone(probed_client, temp_db):
    """This is the behaviour /healthz must NOT have: readiness withdraws the
    replica from rotation, liveness would have killed it."""
    client, _ = probed_client
    await temp_db.close()

    resp = await client.get("/readyz")

    assert resp.status == 503
    assert (await resp.json())["checks"]["database"] is False


@pytest.mark.asyncio
async def test_a_draining_replica_is_not_ready_but_still_alive(probed_client):
    client, probe = probed_client
    probe.begin_draining()

    ready = await client.get("/readyz")
    alive = await client.get("/healthz")

    assert ready.status == 503
    assert (await ready.json())["draining"] is True
    assert alive.status == 200


@pytest.mark.asyncio
async def test_metrics_endpoint_renders_prometheus_text(temp_db):
    registry = MetricsRegistry()
    registry.counter("kfchess_example_total", "An example.").increment(2)
    api = HttpApi(GameQueryService(temp_db), metrics_registry=registry)

    async with TestClient(TestServer(api.build_app())) as client:
        resp = await client.get("/metrics")
        text = await resp.text()

    assert resp.status == 200
    assert resp.headers["Content-Type"].startswith("text/plain")
    assert "kfchess_example_total 2" in text


# ---------------------------------------------------------------------------
# Cache directives
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_replay_is_marked_immutable(client, temp_db):
    """A game row is written once inside one transaction and never updated, so
    its representation can never change — which is what lets a CDN edge answer
    every repeat view without the database being touched at all."""
    game_id = await _seed_game(temp_db)

    resp = await client.get(f"/api/games/{game_id}")

    assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert resp.headers["ETag"] == f'"game-{game_id}"'


@pytest.mark.asyncio
async def test_a_matching_etag_is_answered_304(client, temp_db):
    game_id = await _seed_game(temp_db)
    etag = (await client.get(f"/api/games/{game_id}")).headers["ETag"]

    resp = await client.get(f"/api/games/{game_id}", headers={"If-None-Match": etag})

    assert resp.status == 304


@pytest.mark.asyncio
async def test_the_pgn_representation_is_cached_the_same_way(client, temp_db):
    game_id = await _seed_game(temp_db)

    resp = await client.get(f"/api/games/{game_id}/pgn")

    assert "immutable" in resp.headers["Cache-Control"]
    assert "ROOM01.pgn" in resp.headers["Content-Disposition"]


@pytest.mark.asyncio
async def test_the_leaderboard_is_cacheable_but_not_immutable(client):
    """It changes constantly and matters to nobody within a few seconds, so a
    short shared cache collapses arbitrary read volume into two origin queries
    per minute."""
    resp = await client.get("/api/leaderboard")

    assert resp.headers["Cache-Control"] == "public, max-age=30, stale-while-revalidate=60"


@pytest.mark.asyncio
async def test_the_leaderboard_limit_is_capped_not_obeyed(client, temp_db):
    """An unbounded limit lets one request read and serialize the whole users
    table."""
    await _seed_game(temp_db)

    resp = await client.get("/api/leaderboard?limit=1000000")

    assert resp.status == 200
    assert len(await resp.json()) <= MAX_LEADERBOARD_LIMIT


@pytest.mark.asyncio
async def test_a_non_numeric_leaderboard_limit_is_rejected(client):
    assert (await client.get("/api/leaderboard?limit=all")).status == 400


# ---------------------------------------------------------------------------
# Token issuance
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def auth_client(temp_db):
    """An API with credential checking and token signing wired up."""
    api = HttpApi(
        GameQueryService(temp_db),
        auth_service=AuthService(temp_db),
        token_service=TokenService(signing_key="test-key"),
    )
    async with TestClient(TestServer(api.build_app())) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_token_routes_are_absent_without_a_signing_key(client):
    """A deployment with no key configured must not expose issuance at all,
    rather than issue tokens signed with nothing."""
    resp = await client.post("/api/auth/login", json={"username": "x", "password": "y"})

    assert resp.status == 404


@pytest.mark.asyncio
async def test_login_returns_a_usable_token_pair(auth_client, temp_db):
    await temp_db.create_user("Tokened", "password123", initial_elo=1234)

    resp = await auth_client.post(
        "/api/auth/login", json={"username": "Tokened", "password": "password123"}
    )

    assert resp.status == 200
    body = await resp.json()
    assert body["token_type"] == "Bearer"
    assert body["elo"] == 1234
    claims = TokenService(signing_key="test-key").verify(body["access_token"])
    assert claims.is_ok and claims.value.username == "Tokened"


@pytest.mark.asyncio
async def test_a_token_response_is_never_cached(auth_client, temp_db):
    """One cached response would hand a second caller the first one's token."""
    await temp_db.create_user("Tokened", "password123")

    resp = await auth_client.post(
        "/api/auth/login", json={"username": "Tokened", "password": "password123"}
    )

    assert resp.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_bad_credentials_are_refused_with_401(auth_client, temp_db):
    await temp_db.create_user("Tokened", "password123")

    resp = await auth_client.post(
        "/api/auth/login", json={"username": "Tokened", "password": "wrong"}
    )

    assert resp.status == 401


@pytest.mark.asyncio
async def test_a_refresh_token_yields_a_fresh_pair(auth_client, temp_db):
    await temp_db.create_user("Tokened", "password123")
    login = await (
        await auth_client.post(
            "/api/auth/login", json={"username": "Tokened", "password": "password123"}
        )
    ).json()

    resp = await auth_client.post(
        "/api/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )

    assert resp.status == 200
    assert (await resp.json())["username"] == "Tokened"


@pytest.mark.asyncio
async def test_an_access_token_cannot_be_used_to_refresh(auth_client, temp_db):
    """Accepting one would silently turn hour-long sessions into month-long
    ones."""
    await temp_db.create_user("Tokened", "password123")
    login = await (
        await auth_client.post(
            "/api/auth/login", json={"username": "Tokened", "password": "password123"}
        )
    ).json()

    resp = await auth_client.post(
        "/api/auth/refresh", json={"refresh_token": login["access_token"]}
    )

    assert resp.status == 401


@pytest.mark.asyncio
async def test_a_malformed_login_body_is_rejected(auth_client):
    assert (await auth_client.post("/api/auth/login", data="not json")).status == 400
    assert (await auth_client.post("/api/auth/login", json=["a"])).status == 400
    assert (await auth_client.post("/api/auth/login", json={"username": 1})).status == 400


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_beyond_the_per_caller_budget_are_refused(temp_db):
    """`/api/games/{id}` accepts any integer, so the whole game history can be
    walked by counting up — at whatever rate the caller likes, without this."""
    api = HttpApi(GameQueryService(temp_db), requests_per_minute=2)

    async with TestClient(TestServer(api.build_app())) as client:
        statuses = [(await client.get("/api/games/1")).status for _ in range(3)]

    assert statuses[-1] == 429


@pytest.mark.asyncio
async def test_login_has_a_tighter_budget_than_ordinary_reads(temp_db):
    """It is the one route that costs a bcrypt verification."""
    api = HttpApi(
        GameQueryService(temp_db),
        auth_service=AuthService(temp_db),
        token_service=TokenService(signing_key="test-key"),
        login_attempts_per_minute=1,
    )

    async with TestClient(TestServer(api.build_app())) as client:
        await client.post("/api/auth/login", json={"username": "a", "password": "b"})
        resp = await client.post("/api/auth/login", json={"username": "a", "password": "b"})

    assert resp.status == 429
    assert resp.headers["Retry-After"] == "60"


@pytest.mark.asyncio
async def test_probes_are_never_rate_limited(temp_db):
    """The orchestrator calls these on a fixed schedule; rate-limiting a
    liveness probe gets the replica killed by its own protection."""
    api = HttpApi(GameQueryService(temp_db), requests_per_minute=1)

    async with TestClient(TestServer(api.build_app())) as client:
        statuses = [(await client.get("/healthz")).status for _ in range(5)]
        statuses += [(await client.get("/metrics")).status for _ in range(5)]

    assert set(statuses) == {200}
