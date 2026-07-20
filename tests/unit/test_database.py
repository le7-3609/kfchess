"""Unit tests for Database adapter."""

import os
import pytest
import pytest_asyncio
from server.infrastructure.database.database import Database


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db_file = str(tmp_path / "test.db")
    db = Database(db_file)
    await db.connect()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_create_user(temp_db):
    user_id = await temp_db.create_user("alice", "secret123")
    assert user_id is not None
    assert user_id > 0

    user = await temp_db.get_user_by_username("alice")
    assert user is not None
    assert user[0] == user_id
    assert user[1] == "alice"
    assert user[2] == 1200


@pytest.mark.asyncio
async def test_duplicate_user_rejected(temp_db):
    id1 = await temp_db.create_user("bob", "pass1")
    assert id1 is not None

    id2 = await temp_db.create_user("bob", "pass2")
    assert id2 is None


@pytest.mark.asyncio
async def test_authenticate_user_success(temp_db):
    await temp_db.create_user("charlie", "password_abc")

    auth = await temp_db.authenticate_user("charlie", "password_abc")
    assert auth is not None
    assert auth[1] == "charlie"
    assert auth[2] == 1200


@pytest.mark.asyncio
async def test_authenticate_user_wrong_password(temp_db):
    await temp_db.create_user("charlie", "password_abc")

    auth = await temp_db.authenticate_user("charlie", "wrong_pass")
    assert auth is None


@pytest.mark.asyncio
async def test_update_elo(temp_db):
    await temp_db.create_user("dave", "pass")
    updated = await temp_db.update_elo("dave", 1350)
    assert updated is True

    user = await temp_db.get_user_by_username("dave")
    assert user[2] == 1350
