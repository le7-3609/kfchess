"""SecureQueryExecutor's contract, ported to PostgreSQL and re-proven there.

This is `tests/unit/test_query_executor.py` re-aimed at the other driver. The
contract is the security property — user input is bound and never parsed as SQL,
driver detail never reaches a caller-visible message, a call-site mistake fails
loudly — and porting the adapter without porting these would mean the property
was only ever checked against the database the server stopped using.

The differences are exactly three, and each is a dialect fact rather than a
weakening: `$n` instead of `?`, no `:name` form, and an id that must be asked for
with `RETURNING`.
"""

import pytest
import pytest_asyncio

from server.application.auth_service import AuthService
from server.infrastructure.database.postgres_database import (
    SELECT_USER_PROFILE_SQL,
    PostgresDatabase,
)
from server.infrastructure.database.postgres_query_executor import PostgresQueryExecutor
from server.infrastructure.database.query_contract import (
    GENERIC_ERROR_MESSAGE,
    QueryContractError,
    resolve_identifier,
)

pytestmark = pytest.mark.infra

INJECTION_PAYLOADS = (
    "' OR '1'='1",
    "'; DROP TABLE users; --",
    "admin'--",
    "' UNION SELECT id, username, password_hash, elo FROM users --",
    "x' OR 1=1 /*",
)


@pytest_asyncio.fixture
async def executor(postgres_database):
    return PostgresQueryExecutor(postgres_database._require_pool)


@pytest.mark.asyncio
async def test_injection_payload_is_treated_as_a_literal_username(postgres_database):
    await postgres_database.create_user("alice", "secret123")

    for payload in INJECTION_PAYLOADS:
        assert await postgres_database.authenticate_user(payload, "secret123") is None
        assert await postgres_database.get_user_by_username(payload) is None

    assert await postgres_database.get_user_by_username("alice") is not None


@pytest.mark.asyncio
async def test_stacked_drop_payload_leaves_the_schema_intact(postgres_database):
    await postgres_database.create_user("alice", "secret123")

    await postgres_database.authenticate_user("'; DROP TABLE users; --", "anything")

    assert await postgres_database.get_user_by_username("alice") is not None


@pytest.mark.asyncio
async def test_a_username_may_legitimately_contain_a_quote(postgres_database):
    """Binding means the payload characters are data, not something to strip."""
    user_id = await postgres_database.create_user("o'brien", "secret123")

    profile = await postgres_database.authenticate_user("o'brien", "secret123")

    assert profile == (user_id, "o'brien", 1200)


@pytest.mark.asyncio
async def test_fetch_one_binds_a_quote_bearing_value(executor, postgres_database):
    await postgres_database.create_user("o'brien", "secret123")

    result = await executor.fetch_one(SELECT_USER_PROFILE_SQL, ("o'brien",))

    assert result.is_ok
    assert result.value[1] == "o'brien"


@pytest.mark.asyncio
async def test_database_error_returns_the_generic_message(executor):
    result = await executor.fetch_one("SELECT * FROM no_such_table WHERE id = $1", (1,))

    assert not result.is_ok
    assert result.error == GENERIC_ERROR_MESSAGE


@pytest.mark.asyncio
async def test_error_message_hides_driver_detail(executor):
    result = await executor.fetch_one("SELECT nope FROM users WHERE id = $1", (1,))

    assert "does not exist" not in result.error.lower()
    assert "users" not in result.error.lower()


@pytest.mark.asyncio
async def test_unknown_user_and_wrong_password_answer_identically(postgres_database):
    await postgres_database.create_user("alice", "secret123")
    service = AuthService(postgres_database)

    missing = await service.login("nobody", "secret123")
    wrong = await service.login("alice", "wrong_password")

    assert not missing.is_ok and not wrong.is_ok
    assert missing.error == wrong.error


@pytest.mark.asyncio
async def test_stacked_statement_is_refused(executor):
    with pytest.raises(QueryContractError):
        await executor.execute("DELETE FROM users WHERE id = $1; DROP TABLE users", (1,))


@pytest.mark.asyncio
async def test_semicolon_inside_a_literal_is_not_a_second_statement(executor):
    result = await executor.fetch_one("SELECT 'a;b' WHERE $1 = 1", (1,))

    assert result.value == ("a;b",)


@pytest.mark.asyncio
async def test_parameter_arity_mismatch_is_refused(executor):
    with pytest.raises(QueryContractError):
        await executor.fetch_one("SELECT id FROM users WHERE username = $1", ())

    with pytest.raises(QueryContractError):
        await executor.fetch_one("SELECT id FROM users WHERE username = $1", ("a", "b"))


@pytest.mark.asyncio
async def test_bare_string_parameters_are_refused(executor):
    with pytest.raises(QueryContractError):
        await executor.fetch_one("SELECT id FROM users WHERE username = $1", "alice")


@pytest.mark.asyncio
async def test_unbindable_object_is_refused(executor):
    class SqlLookalike:
        def __str__(self) -> str:
            return "1 OR 1=1"

    with pytest.raises(QueryContractError):
        await executor.fetch_one("SELECT id FROM users WHERE id = $1", (SqlLookalike(),))


@pytest.mark.asyncio
async def test_named_parameters_are_refused(executor):
    """asyncpg has no `:name` form, so a mapping must fail loudly.

    The SQLite gate accepts one; silently reinterpreting it here would bind
    nothing and run a statement with an unfilled placeholder.
    """
    with pytest.raises(QueryContractError):
        await executor.fetch_one(
            "SELECT id FROM users WHERE username = $1", {"name": "alice"}
        )


@pytest.mark.asyncio
async def test_empty_statement_is_refused(executor):
    with pytest.raises(QueryContractError):
        await executor.fetch_one("   ")


@pytest.mark.asyncio
async def test_execute_reports_rowcount(executor, postgres_database):
    await postgres_database.create_user("alice", "secret123")

    result = await executor.execute(
        "UPDATE users SET elo = $1 WHERE username = $2", (1400, "alice")
    )

    rowcount, _ = result.value
    assert rowcount == 1


@pytest.mark.asyncio
async def test_insert_returning_id_maps_a_conflict_to_none(executor, postgres_database):
    await postgres_database.create_user("alice", "secret123")

    conflict = await executor.insert_returning_id(
        "INSERT INTO users (username, password_hash, elo) VALUES ($1, $2, $3) "
        "ON CONFLICT (username) DO NOTHING RETURNING id",
        ("alice", "hash", 1200),
    )

    assert conflict.is_ok
    assert conflict.value is None


@pytest.mark.asyncio
async def test_a_constraint_violation_also_maps_to_none(executor, postgres_database):
    """Without `ON CONFLICT`, the unique index raises instead — and that too is
    an answerable business result, not a leak-worthy failure."""
    await postgres_database.create_user("alice", "secret123")

    conflict = await executor.insert_returning_id(
        "INSERT INTO users (username, password_hash, elo) VALUES ($1, $2, $3) RETURNING id",
        ("alice", "hash", 1200),
    )

    assert conflict.is_ok
    assert conflict.value is None


@pytest.mark.asyncio
async def test_execute_many_binds_every_row(executor, postgres_database):
    result = await executor.execute_many(
        "INSERT INTO users (username, password_hash, elo) VALUES ($1, $2, $3)",
        [("a'b", "h", 1200), ("c;d", "h", 1200)],
    )

    assert result.is_ok
    assert await postgres_database.get_user_by_username("c;d") is not None


@pytest.mark.asyncio
async def test_executor_requires_an_open_pool(postgres_dsn):
    database = PostgresDatabase(dsn=postgres_dsn)
    executor = PostgresQueryExecutor(database._require_pool)

    with pytest.raises(RuntimeError):
        await executor.fetch_one("SELECT 1 WHERE $1 = 1", (1,))


@pytest.mark.asyncio
async def test_a_placeholder_may_repeat_without_inflating_arity(executor, postgres_database):
    """`$1` twice binds one value. Counting occurrences, the way the `?` gate
    must, would reject this correct statement — which is why the PostgreSQL gate
    derives arity from the highest index instead."""
    await postgres_database.create_user("alice", "secret123")

    result = await executor.fetch_one(
        "SELECT username FROM users WHERE username = $1 OR username = $1", ("alice",)
    )

    assert result.value == ("alice",)


@pytest.mark.asyncio
async def test_a_gap_in_the_placeholder_sequence_is_refused(executor):
    """`$1, $3` with two values would bind the wrong value to `$3`."""
    with pytest.raises(QueryContractError):
        await executor.fetch_one("SELECT $1 WHERE $3 = 1", ("a", "b"))


@pytest.mark.asyncio
async def test_oversized_credentials_are_refused_before_the_database(postgres_database):
    service = AuthService(postgres_database)

    result = await service.register("a" * 500, "secret123")

    assert not result.is_ok
    assert await postgres_database.get_user_by_username("a" * 500) is None


def test_resolve_identifier_accepts_an_allowlisted_name():
    assert resolve_identifier("elo", {"elo", "username"}).value == "elo"


def test_resolve_identifier_rejects_anything_else():
    result = resolve_identifier("elo; DROP TABLE users", {"elo", "username"})

    assert not result.is_ok
    assert result.error == GENERIC_ERROR_MESSAGE
