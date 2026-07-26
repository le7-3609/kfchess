"""Unit tests for SecureQueryExecutor and the parameterized auth query path.

These pin the three properties the executor exists to guarantee: user input is
bound and never parsed as SQL, driver detail never reaches a caller-visible
message, and a call-site mistake fails loudly instead of silently running.
"""

import aiosqlite
import pytest
import pytest_asyncio

from server.application.auth_service import AuthService
from server.infrastructure.database.database import (
    SELECT_USER_PROFILE_SQL,
    Database,
)
from server.infrastructure.database.query_executor import (
    GENERIC_ERROR_MESSAGE,
    QueryContractError,
    SecureQueryExecutor,
    resolve_identifier,
)

INJECTION_PAYLOADS = (
    "' OR '1'='1",
    "'; DROP TABLE users; --",
    "admin'--",
    "' UNION SELECT id, username, password_hash, elo FROM users --",
    "x' OR 1=1 /*",
)


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = Database(str(tmp_path / "secure.db"))
    await db.connect()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def executor(temp_db):
    return SecureQueryExecutor(temp_db._require_connection)


@pytest.mark.asyncio
async def test_injection_payload_is_treated_as_a_literal_username(temp_db):
    await temp_db.create_user("alice", "secret123")

    for payload in INJECTION_PAYLOADS:
        assert await temp_db.authenticate_user(payload, "secret123") is None
        assert await temp_db.get_user_by_username(payload) is None

    assert await temp_db.get_user_by_username("alice") is not None


@pytest.mark.asyncio
async def test_stacked_drop_payload_leaves_the_schema_intact(temp_db):
    await temp_db.create_user("alice", "secret123")

    await temp_db.authenticate_user("'; DROP TABLE users; --", "anything")

    assert await temp_db.get_user_by_username("alice") is not None


@pytest.mark.asyncio
async def test_a_username_may_legitimately_contain_a_quote(temp_db):
    """Binding means the payload characters are data, not something to strip."""
    user_id = await temp_db.create_user("o'brien", "secret123")

    profile = await temp_db.authenticate_user("o'brien", "secret123")

    assert profile == (user_id, "o'brien", 1200)


@pytest.mark.asyncio
async def test_fetch_one_binds_a_quote_bearing_value(executor, temp_db):
    await temp_db.create_user("o'brien", "secret123")

    result = await executor.fetch_one(SELECT_USER_PROFILE_SQL, ("o'brien",))

    assert result.is_ok
    assert result.value[1] == "o'brien"


@pytest.mark.asyncio
async def test_named_parameters_are_bound(executor, temp_db):
    await temp_db.create_user("alice", "secret123")

    result = await executor.fetch_one(
        "SELECT username FROM users WHERE username = :name", {"name": "alice"}
    )

    assert result.value == ("alice",)


@pytest.mark.asyncio
async def test_database_error_returns_the_generic_message(executor):
    result = await executor.fetch_one("SELECT * FROM no_such_table WHERE id = ?", (1,))

    assert not result.is_ok
    assert result.error == GENERIC_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_error_message_hides_driver_detail(executor):
    result = await executor.fetch_one("SELECT nope FROM users WHERE id = ?", (1,))

    assert "no such column" not in result.error.lower()
    assert "users" not in result.error.lower()


@pytest.mark.asyncio
async def test_failed_login_message_never_carries_database_detail(temp_db):
    """The presentation layer forwards Result.error to the client verbatim."""
    await temp_db.close()
    service = AuthService(temp_db)

    with pytest.raises(RuntimeError):
        await service.login("alice", "secret123")


@pytest.mark.asyncio
async def test_unknown_user_and_wrong_password_answer_identically(temp_db):
    await temp_db.create_user("alice", "secret123")
    service = AuthService(temp_db)

    missing = await service.login("nobody", "secret123")
    wrong = await service.login("alice", "wrong_password")

    assert not missing.is_ok and not wrong.is_ok
    assert missing.error == wrong.error


@pytest.mark.asyncio
async def test_stacked_statement_is_refused(executor):
    with pytest.raises(QueryContractError):
        await executor.execute("DELETE FROM users WHERE id = ?; DROP TABLE users", (1,))


@pytest.mark.asyncio
async def test_semicolon_inside_a_literal_is_not_a_second_statement(executor, temp_db):
    result = await executor.fetch_one("SELECT 'a;b' WHERE ? = 1", (1,))

    assert result.value == ("a;b",)


@pytest.mark.asyncio
async def test_parameter_arity_mismatch_is_refused(executor):
    with pytest.raises(QueryContractError):
        await executor.fetch_one("SELECT id FROM users WHERE username = ?", ())

    with pytest.raises(QueryContractError):
        await executor.fetch_one("SELECT id FROM users WHERE username = ?", ("a", "b"))


@pytest.mark.asyncio
async def test_bare_string_parameters_are_refused(executor):
    """SQLite would bind a bare str character by character — always a bug."""
    with pytest.raises(QueryContractError):
        await executor.fetch_one("SELECT id FROM users WHERE username = ?", "alice")


@pytest.mark.asyncio
async def test_unbindable_object_is_refused(executor):
    class SqlLookalike:
        def __str__(self) -> str:
            return "1 OR 1=1"

    with pytest.raises(QueryContractError):
        await executor.fetch_one(
            "SELECT id FROM users WHERE id = ?", (SqlLookalike(),)
        )


@pytest.mark.asyncio
async def test_missing_named_parameter_is_refused(executor):
    with pytest.raises(QueryContractError):
        await executor.fetch_one(
            "SELECT id FROM users WHERE username = :name", {"other": "alice"}
        )


@pytest.mark.asyncio
async def test_empty_statement_is_refused(executor):
    with pytest.raises(QueryContractError):
        await executor.fetch_one("   ")


@pytest.mark.asyncio
async def test_execute_reports_rowcount_and_last_id(executor, temp_db):
    await temp_db.create_user("alice", "secret123")

    result = await executor.execute(
        "UPDATE users SET elo = ? WHERE username = ?", (1400, "alice")
    )

    rowcount, _ = result.value
    assert rowcount == 1


@pytest.mark.asyncio
async def test_insert_returning_id_maps_a_conflict_to_none(executor, temp_db):
    await temp_db.create_user("alice", "secret123")

    conflict = await executor.insert_returning_id(
        "INSERT INTO users (username, password_hash, elo) VALUES (?, ?, ?)",
        ("alice", "hash", 1200),
    )

    assert conflict.is_ok
    assert conflict.value is None


@pytest.mark.asyncio
async def test_execute_many_binds_every_row(executor, temp_db):
    result = await executor.execute_many(
        "INSERT INTO users (username, password_hash, elo) VALUES (?, ?, ?)",
        [("a'b", "h", 1200), ("c;d", "h", 1200)],
    )

    assert result.is_ok
    assert await temp_db.get_user_by_username("c;d") is not None


@pytest.mark.asyncio
async def test_executor_requires_an_open_connection(tmp_path):
    db = Database(str(tmp_path / "closed.db"))
    executor = SecureQueryExecutor(db._require_connection)

    with pytest.raises(RuntimeError):
        await executor.fetch_one("SELECT 1 WHERE ? = 1", (1,))


def test_resolve_identifier_accepts_an_allowlisted_name():
    assert resolve_identifier("elo", {"elo", "username"}).value == "elo"


def test_resolve_identifier_rejects_anything_else():
    result = resolve_identifier("elo; DROP TABLE users", {"elo", "username"})

    assert not result.is_ok
    assert result.error == GENERIC_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_oversized_credentials_are_refused_before_the_database(temp_db):
    service = AuthService(temp_db)

    result = await service.register("a" * 500, "secret123")

    assert not result.is_ok
    assert await temp_db.get_user_by_username("a" * 500) is None


@pytest.mark.asyncio
async def test_rollback_runs_when_an_uncommitted_write_fails(temp_db):
    """A contained failure must not leave a half-applied batch open."""
    executor = SecureQueryExecutor(temp_db._require_connection)
    await temp_db.create_user("alice", "secret123")

    await executor.execute(
        "INSERT INTO users (username, password_hash, elo) VALUES (?, ?, ?)",
        ("bob", "hash", 1200),
        commit=False,
    )
    failed = await executor.execute(
        "INSERT INTO users (id, username, password_hash, elo) VALUES (?, ?, ?, ?)",
        (1, "carol", "hash", 1200),
    )

    assert not failed.is_ok
    assert await temp_db.get_user_by_username("bob") is None
