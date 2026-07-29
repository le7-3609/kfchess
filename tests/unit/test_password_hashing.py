"""Unit tests for password hashing policy.

Three properties matter here and none of them is "the password verifies": that
much already worked. What is asserted is that the work factor is a deliberate
number, that it can be raised for accounts that already exist, and that the
hashing does not run on the event loop that drives every game's tick.
"""

import asyncio
import time

import bcrypt
import pytest
import pytest_asyncio

from server.infrastructure.database.database import BCRYPT_COST_FACTOR, Database

_PASSWORD = "password123"


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = Database(str(tmp_path / "hash.db"))
    await db.connect()
    yield db
    await db.close()


async def _stored_hash(db: Database, username: str) -> str:
    conn = db._require_connection()
    async with conn.execute(
        "SELECT password_hash FROM users WHERE username = ?", (username,)
    ) as cursor:
        return (await cursor.fetchone())[0]


@pytest.mark.asyncio
async def test_a_new_password_is_hashed_at_the_pinned_cost(temp_db):
    """`bcrypt.gensalt()`'s default has moved between library releases, so the
    factor protecting every password must be pinned rather than inherited from
    whichever version the image resolved."""
    await temp_db.create_user("Alice", _PASSWORD)

    assert Database._cost_of(await _stored_hash(temp_db, "Alice")) == BCRYPT_COST_FACTOR


@pytest.mark.asyncio
async def test_the_cost_factor_is_read_back_off_a_hash(temp_db):
    weak = bcrypt.hashpw(_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()

    assert Database._cost_of(weak) == 4


@pytest.mark.asyncio
async def test_an_unreadable_hash_reports_a_cost_below_any_target(temp_db):
    """So it is rehashed on the next successful login rather than left alone."""
    assert Database._cost_of("not-a-bcrypt-hash") == 0
    assert Database._cost_of("") == 0


@pytest.mark.asyncio
async def test_a_login_rehashes_a_password_stored_below_the_target_cost(temp_db):
    """Without this the cost factor can only ever apply to accounts created
    after it was raised. Login is the one moment the plaintext is available to
    rehash from."""
    await temp_db.create_user("Legacy", _PASSWORD)
    weak = bcrypt.hashpw(_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    await temp_db._queries.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?", (weak, "Legacy")
    )
    assert Database._cost_of(await _stored_hash(temp_db, "Legacy")) == 4

    assert await temp_db.authenticate_user("Legacy", _PASSWORD) is not None

    assert Database._cost_of(await _stored_hash(temp_db, "Legacy")) == BCRYPT_COST_FACTOR
    # The upgraded hash still authenticates the same password.
    assert await temp_db.authenticate_user("Legacy", _PASSWORD) is not None


@pytest.mark.asyncio
async def test_a_hash_already_at_the_target_cost_is_left_alone(temp_db):
    await temp_db.create_user("Current", _PASSWORD)
    before = await _stored_hash(temp_db, "Current")

    await temp_db.authenticate_user("Current", _PASSWORD)

    assert await _stored_hash(temp_db, "Current") == before


@pytest.mark.asyncio
async def test_a_failed_login_never_rehashes(temp_db):
    """Rehashing on a wrong password would overwrite a valid credential with a
    hash of the attacker's guess."""
    await temp_db.create_user("Alice", _PASSWORD)
    before = await _stored_hash(temp_db, "Alice")

    assert await temp_db.authenticate_user("Alice", "wrong") is None

    assert await _stored_hash(temp_db, "Alice") == before


@pytest.mark.asyncio
async def test_a_corrupt_stored_credential_reads_as_a_failed_login(temp_db):
    """Not as an exception escaping into the auth path."""
    await temp_db.create_user("Broken", _PASSWORD)
    await temp_db._queries.execute(
        "UPDATE users SET password_hash = ? WHERE username = ?", ("garbage", "Broken")
    )

    assert await temp_db.authenticate_user("Broken", _PASSWORD) is None


@pytest.mark.asyncio
async def test_hashing_does_not_block_the_event_loop(temp_db):
    """bcrypt at cost 12 occupies a core for a noticeable fraction of a second.
    Run inline it would stall the loop that drives every room's tick — five
    missed ticks in every game on the process, per login. This measures the
    loop's responsiveness *while* a hash is in progress.
    """
    delays = []

    async def sample_loop_responsiveness() -> None:
        for _ in range(20):
            before = time.monotonic()
            await asyncio.sleep(0.01)
            delays.append(time.monotonic() - before)

    watcher = asyncio.ensure_future(sample_loop_responsiveness())
    await temp_db.create_user("Blocking", _PASSWORD)
    await temp_db.authenticate_user("Blocking", _PASSWORD)
    await watcher

    # A blocked loop shows up as one enormous gap; a threaded hash leaves every
    # sleep close to its nominal duration.
    assert max(delays) < 0.15, f"event loop stalled for {max(delays):.3f}s during hashing"
