"""Fixtures for the infra suite: a real Redis, a real PostgreSQL, a real NATS.

Each fixture skips rather than fails when its service is unconfigured, so the
default `pytest` run on a machine with no Docker daemon is green.

Every fixture also isolates its test. Redis gets a per-test key prefix and
PostgreSQL gets its rows deleted between tests, because these services are
shared and long-lived — unlike a `tmp_path` SQLite file, they carry the previous
test's state into the next one unless something removes it.
"""

import os
import uuid

import pytest
import pytest_asyncio

ENV_REDIS_URL = "KFCHESS_REDIS_URL"
ENV_POSTGRES_DSN = "KFCHESS_POSTGRES_DSN"
ENV_NATS_URL = "KFCHESS_NATS_URL"


def _require(env_name: str, service: str) -> str:
    value = os.environ.get(env_name)
    if not value:
        pytest.skip(f"{env_name} is not set; no {service} to test against")
    return value


@pytest.fixture
def redis_url() -> str:
    return _require(ENV_REDIS_URL, "Redis")


@pytest.fixture
def postgres_dsn() -> str:
    return _require(ENV_POSTGRES_DSN, "PostgreSQL")


@pytest.fixture
def nats_url() -> str:
    return _require(ENV_NATS_URL, "NATS")


@pytest_asyncio.fixture
async def redis_connection(redis_url):
    """A connection namespaced to this test alone.

    The prefix is a fresh uuid per test rather than a fixed one that is flushed:
    `FLUSHDB` would delete whatever else is using the developer's local Redis,
    and two tests running in parallel against a shared instance would delete
    each other's keys.
    """
    from server.infrastructure.coordination.redis_client import RedisConnection

    connection = RedisConnection(url=redis_url, key_prefix=f"kfctest:{uuid.uuid4().hex[:8]}")
    if not await connection.ping():
        await connection.close()
        pytest.skip(f"Redis at {redis_url} did not answer a ping")
    yield connection
    await _delete_prefixed_keys(connection)
    await connection.close()


async def _delete_prefixed_keys(connection) -> None:
    cursor = 0
    while True:
        cursor, keys = await connection.client.scan(cursor, match=connection.key("*"), count=500)
        if keys:
            await connection.client.delete(*keys)
        if cursor == 0:
            return


@pytest_asyncio.fixture
async def postgres_database(postgres_dsn):
    """A connected PostgresDatabase over an emptied schema.

    Truncating up front rather than afterwards means a test that fails leaves
    its rows behind to be inspected, and the next run still starts clean.
    """
    from server.infrastructure.database.postgres_database import PostgresDatabase

    database = PostgresDatabase(dsn=postgres_dsn)
    await database.connect()
    await _truncate_all(database)
    yield database
    await database.close()


async def _truncate_all(database) -> None:
    """Empty every table in one statement.

    `TRUNCATE ... CASCADE` rather than per-table `DELETE`: the foreign keys make
    delete order significant, and `RESTART IDENTITY` keeps ids small and
    predictable across runs.
    """
    async with database._require_pool().acquire() as connection:
        await connection.execute(
            "TRUNCATE game_statistics, moves, games, users RESTART IDENTITY CASCADE"
        )
