"""Unit tests for AuthService."""

import pytest
import pytest_asyncio
from server.application.auth_service import AuthService
from server.infrastructure.database.database import Database


@pytest_asyncio.fixture
async def temp_auth_service(tmp_path):
    db_file = str(tmp_path / "auth_test.db")
    db = Database(db_file)
    await db.connect()
    auth_service = AuthService(db)
    yield auth_service
    await db.close()


@pytest.mark.asyncio
async def test_register_and_login_success(temp_auth_service):
    reg_res = await temp_auth_service.register("player1", "password123")
    assert reg_res.is_ok
    user_id, username, elo = reg_res.value
    assert username == "player1"
    assert elo == 1200

    login_res = await temp_auth_service.login("player1", "password123")
    assert login_res.is_ok
    assert login_res.value[1] == "player1"


@pytest.mark.asyncio
async def test_register_short_username_or_password(temp_auth_service):
    res1 = await temp_auth_service.register("ab", "password123")
    assert not res1.is_ok

    res2 = await temp_auth_service.register("player2", "123")
    assert not res2.is_ok


@pytest.mark.asyncio
async def test_login_invalid_credentials(temp_auth_service):
    res = await temp_auth_service.login("nonexistent", "pass")
    assert not res.is_ok
