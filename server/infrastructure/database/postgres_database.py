"""PostgreSQL adapter — the same persistence surface as `Database`, over asyncpg.

Owns: the connection pool, and the PostgreSQL rendering of every statement the
server runs.
Must not own: the schema (Alembic owns it — see `migrations/`), password hashing
policy (`password_hashing.py`), game rules, WebSocket sessions, or the decision
of *which* games are eligible to persist (that gate is
`GamePersistenceService`).

**Why the port.** SQLite is not outgrown by row count — 100M rows is comfortable
for any server-based RDBMS — but by three structural limits: one writer at a
time even in WAL mode, no replication or sharding, and an embedded rather than
client-server design that requires every reader to share a filesystem. None of
those can be worked around by a fleet of hundreds of processes on many hosts.

**What changed from the SQLite adapter, and nothing else.**

* `?` becomes `$n`, checked by `PostgresQueryExecutor` against the same contract.
* `INTEGER PRIMARY KEY AUTOINCREMENT` becomes `BIGSERIAL`, and `lastrowid`
  becomes an explicit `RETURNING id` — PostgreSQL has no out-of-band way to
  learn an inserted id.
* One connection becomes a pool, so a slow query occupies one connection rather
  than blocking every other caller behind the single shared one.
* `connect()` no longer creates tables. Fifty replicas running
  `CREATE TABLE IF NOT EXISTS` against one database at startup is a race with no
  upside; the schema is applied once, by a job that runs to completion before any
  replica starts.

Timestamps are bound as ISO-8601 strings and cast in the statement rather than
as `datetime` objects, so `BINDABLE_TYPES` stays as narrow as it is on the SQLite
path — widening it to admit `datetime` would also admit every other object a
driver happens to know how to adapt.

The cast is written `$n::text::timestamptz`, not `$n::timestamptz`. asyncpg asks
PostgreSQL to infer each parameter's type and then encodes the value client-side
against that answer, so the single-step form makes it infer `timestamptz` and
refuse the string before the cast is ever reached. Pinning the parameter to
`text` first is what lets the value arrive as text and be cast server-side.
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple

try:
    import asyncpg
except ImportError:  # pragma: no cover - exercised only where asyncpg is absent
    asyncpg = None  # type: ignore[assignment]

from server.domain.matchmaking.elo import DEFAULT_PLAYER_ELO
from server.infrastructure.database.database import (
    DEFAULT_LEADERBOARD_LIMIT,
    SavableGame,
    SavableMove,
    SaveOutcome,
)
from server.infrastructure.database.password_hashing import (
    BCRYPT_COST_FACTOR,
    hash_password,
    needs_rehash,
    verify_password,
)
from server.infrastructure.database.postgres_query_executor import PostgresQueryExecutor

_LOGGER = logging.getLogger(__name__)

DEFAULT_POSTGRES_DSN = "postgresql://kfchess@localhost:5432/kfchess"

# Sized for a container, not for the database. Every replica holds its own pool,
# so the fleet's total connection count is (replicas x max_size); PostgreSQL's
# own default limit is 100, which a careless pool size per replica exhausts long
# before any single replica is busy.
DEFAULT_POOL_MIN_SIZE = 2
DEFAULT_POOL_MAX_SIZE = 10

# Bounds how long a caller waits for a free connection before the request is
# answered as a failure. Without it, a stalled database turns into unbounded
# queueing inside the process, which reads as a hang rather than an error.
DEFAULT_POOL_TIMEOUT_SECONDS = 5.0

INSERT_USER_SQL = (
    "INSERT INTO users (username, password_hash, elo) VALUES ($1, $2, $3) "
    "ON CONFLICT (username) DO NOTHING RETURNING id"
)

SELECT_USER_CREDENTIALS_SQL = (
    "SELECT id, username, password_hash, elo FROM users WHERE username = $1"
)

SELECT_USER_PROFILE_SQL = "SELECT id, username, elo FROM users WHERE username = $1"

UPDATE_USER_ELO_SQL = "UPDATE users SET elo = $1 WHERE username = $2"

UPDATE_PASSWORD_HASH_SQL = "UPDATE users SET password_hash = $1 WHERE username = $2"

SELECT_GAME_ID_BY_ROOM_SQL = "SELECT id FROM games WHERE room_id = $1"

# The cheapest statement that proves the pool can still round-trip, used by the
# readiness probe. Deliberately touches no table: readiness asks whether this
# replica can reach its database, not whether the schema is populated.
PING_SQL = "SELECT 1"

# `DO NOTHING` rather than `DO UPDATE`: a room that is already saved is already
# correct, and re-writing it would let a redelivered persistence event overwrite
# a finished game with a second computation of the same result. The `RETURNING`
# clause then yields no row on conflict, which is how a duplicate is recognised.
INSERT_GAME_SQL = """
INSERT INTO games (
    room_id, white_player_id, black_player_id, winner_id, result,
    white_elo_before, white_elo_after, black_elo_before, black_elo_after,
    started_at, ended_at
) VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::text::timestamptz, $11::text::timestamptz
)
ON CONFLICT (room_id) DO NOTHING
RETURNING id
"""

INSERT_MOVE_SQL = """
INSERT INTO moves (
    game_id, move_number, from_square, to_square,
    piece_type, piece_color, captured_piece, timestamp
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""

# Recomputed from the games table rather than incremented, so the cache stays
# correct even if a game is ever re-saved or deleted. `$1` appears many times
# and binds one value, which is exactly why the executor derives arity from the
# highest placeholder index rather than from a count of occurrences.
RECOMPUTE_STATISTICS_SQL = """
INSERT INTO game_statistics (
    user_id, total_games, wins, losses, draws, elo_peak, elo_low, updated_at
)
SELECT
    $1,
    COUNT(*),
    COALESCE(SUM(CASE WHEN winner_id = $1 THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN winner_id IS NOT NULL AND winner_id <> $1 THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN winner_id IS NULL THEN 1 ELSE 0 END), 0),
    MAX(CASE WHEN white_player_id = $1 THEN white_elo_after ELSE black_elo_after END),
    MIN(CASE WHEN white_player_id = $1 THEN white_elo_after ELSE black_elo_after END),
    NOW()
FROM games
WHERE white_player_id = $1 OR black_player_id = $1
ON CONFLICT (user_id) DO UPDATE SET
    total_games = excluded.total_games,
    wins = excluded.wins,
    losses = excluded.losses,
    draws = excluded.draws,
    elo_peak = excluded.elo_peak,
    elo_low = excluded.elo_low,
    updated_at = NOW()
"""

SELECT_STATISTICS_SQL = (
    "SELECT total_games, wins, losses, draws, elo_peak, elo_low "
    "FROM game_statistics WHERE user_id = $1"
)

SELECT_GAME_SQL = """
SELECT
    g.id, g.room_id, g.white_player_id, g.black_player_id,
    g.winner_id, g.result,
    g.white_elo_before, g.white_elo_after,
    g.black_elo_before, g.black_elo_after,
    g.started_at, g.ended_at,
    w.username AS white_username, b.username AS black_username
FROM games g
JOIN users w ON w.id = g.white_player_id
JOIN users b ON b.id = g.black_player_id
WHERE g.id = $1
"""

SELECT_MOVES_SQL = """
SELECT move_number, from_square, to_square,
       piece_type, piece_color, captured_piece, timestamp
FROM moves
WHERE game_id = $1
ORDER BY move_number
"""

# The JOIN (not LEFT JOIN) excludes freshly-registered users who have never
# finished a game, matching what a leaderboard should show.
SELECT_LEADERBOARD_SQL = """
SELECT u.username, u.elo, s.total_games, s.wins
FROM users u
JOIN game_statistics s ON s.user_id = u.id
ORDER BY u.elo DESC, u.username ASC
LIMIT $1
"""


class PostgresDatabase:
    """Async PostgreSQL persistence adapter, pool-backed."""

    def __init__(
        self,
        dsn: str = DEFAULT_POSTGRES_DSN,
        min_size: int = DEFAULT_POOL_MIN_SIZE,
        max_size: int = DEFAULT_POOL_MAX_SIZE,
        pool_timeout_seconds: float = DEFAULT_POOL_TIMEOUT_SECONDS,
    ) -> None:
        if asyncpg is None:
            raise RuntimeError(
                "The 'asyncpg' package is required for the PostgreSQL adapter; "
                "install it or point --db-path at a SQLite file instead."
            )
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool_timeout_seconds = pool_timeout_seconds
        self._pool = None
        # Bound to the accessor, not the pool object, so the executor always
        # sees the currently open pool and fails loudly if it is closed.
        self._queries = PostgresQueryExecutor(self._require_pool)

    async def connect(self) -> None:
        """Open the connection pool.

        Deliberately does not create or migrate the schema. `CREATE TABLE IF NOT
        EXISTS` on every startup is safe for a file one process owns and wrong
        for a shared database fifty replicas start against at once; the schema
        arrives from the Alembic job that runs before any of them.
        """
        if self._pool is not None:
            return
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            timeout=self._pool_timeout_seconds,
            command_timeout=self._pool_timeout_seconds,
        )
        _LOGGER.info(
            "PostgreSQL pool opened (min=%d, max=%d)", self._min_size, self._max_size
        )

    async def close(self) -> None:
        """Gracefully drain and close the pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            _LOGGER.info("PostgreSQL pool closed")

    def _require_pool(self):
        if self._pool is None:
            raise RuntimeError("PostgreSQL pool is not open. Call connect() first.")
        return self._pool

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    async def ping(self) -> bool:
        """Report whether the pool can still answer a trivial query.

        Readiness (not liveness) depends on this: a replica that cannot reach its
        database should stop being handed new work, while the games already
        running on it continue.
        """
        if self._pool is None:
            return False
        result = await self._queries.fetch_one(PING_SQL)
        return result.is_ok and result.value is not None

    async def create_user(
        self, username: str, password_plain: str, initial_elo: int = DEFAULT_PLAYER_ELO
    ) -> Optional[int]:
        """Hash password with bcrypt and insert a new user.

        Returns:
            user_id of created user, or None if the username is taken or the
            insert failed. The two are indistinguishable to the caller by
            design; the real reason is in the server log.
        """
        pw_hash = await hash_password(password_plain)

        insert = await self._queries.insert_returning_id(
            INSERT_USER_SQL, (username, pw_hash, initial_elo)
        )
        if not insert.is_ok:
            return None
        if insert.value is None:
            _LOGGER.warning("Attempted duplicate user registration: %s", username)
        return insert.value

    async def authenticate_user(
        self, username: str, password_plain: str
    ) -> Optional[Tuple[int, str, int]]:
        """Verify username and password.

        The username is bound as a parameter, so a value like ``' OR '1'='1`` is
        compared as a literal name and matches nothing.

        Returns:
            Tuple of (user_id, username, elo) if authenticated, or None. A lookup
            failure also yields None: an unavailable database must read as "not
            authenticated" rather than surfacing why.
        """
        lookup = await self._queries.fetch_one(SELECT_USER_CREDENTIALS_SQL, (username,))
        if not lookup.is_ok or lookup.value is None:
            return None

        user_id, name, pw_hash, elo = lookup.value
        if not await verify_password(password_plain, pw_hash):
            return None

        await self._rehash_if_below_target_cost(name, pw_hash, password_plain)
        return (user_id, name, elo)

    async def _rehash_if_below_target_cost(
        self, username: str, stored_hash: str, password_plain: str
    ) -> None:
        """Upgrade a hash that predates the current cost factor, on login.

        A failure to write the new hash is deliberately not fatal: the login
        already succeeded against a valid hash, and refusing it over a failed
        optimisation would turn a stale cost factor into an outage.
        """
        if not needs_rehash(stored_hash):
            return

        upgraded = await hash_password(password_plain)
        update = await self._queries.execute(UPDATE_PASSWORD_HASH_SQL, (upgraded, username))
        if update.is_ok:
            _LOGGER.info("Rehashed %s's password at cost %d", username, BCRYPT_COST_FACTOR)

    async def get_user_by_username(self, username: str) -> Optional[Tuple[int, str, int]]:
        """Fetch user profile (user_id, username, elo)."""
        lookup = await self._queries.fetch_one(SELECT_USER_PROFILE_SQL, (username,))
        if not lookup.is_ok or lookup.value is None:
            return None
        user_id, name, elo = lookup.value
        return (user_id, name, elo)

    async def update_elo(self, username: str, new_elo: int) -> bool:
        """Update a user's ELO rating.

        Absolute, never incremental — that is what makes replaying the write
        produce the same row, and it is why an at-least-once persistence
        pipeline can redeliver a finished game without corrupting a rating.
        """
        update = await self._queries.execute(UPDATE_USER_ELO_SQL, (new_elo, username))
        if not update.is_ok:
            return False
        rowcount, _ = update.value
        return rowcount > 0

    async def save_completed_game(
        self, game: SavableGame, moves: List[SavableMove]
    ) -> Optional[SaveOutcome]:
        """Persist a finished game, its moves, and both players' refreshed stats.

        The whole operation is one transaction: the game row, every move row, and
        the recomputed statistics for both players either all land or none do.

        Saving the *same* room twice is not a failure. A natural game end and a
        disconnect forfeit can race the same room, and a redelivered persistence
        event does the same. The insert therefore does nothing on a `room_id`
        conflict and the existing id is returned, flagged as pre-existing — so the
        caller can tell "already saved" (success) from "could not save" (None).

        Returns:
            A SaveOutcome naming the game's id, or None if the save was rolled
            back.
        """
        try:
            async with self._require_pool().acquire() as connection:
                async with connection.transaction():
                    outcome = await self._insert_game_row(connection, game)
                    if outcome.already_existed:
                        _LOGGER.info(
                            "Room %s is already persisted as game %s; save was a no-op",
                            game.room_id, outcome.game_id,
                        )
                        return outcome

                    await self._insert_move_rows(connection, outcome.game_id, moves)
                    await self._recompute_statistics(connection, game.white_player_id)
                    await self._recompute_statistics(connection, game.black_player_id)
            _LOGGER.info(
                "Persisted game %s (room %s) with %d moves",
                outcome.game_id, game.room_id, len(moves),
            )
            return outcome
        except Exception as exc:
            # asyncpg's `transaction()` context manager has already rolled back
            # by the time this runs; there is nothing left to undo here.
            _LOGGER.exception("Rolled back save of room %s: %s", game.room_id, exc)
            return None

    async def _insert_game_row(self, connection, game: SavableGame) -> SaveOutcome:
        """Insert the game row, or resolve the id of the one already there."""
        inserted = await self._queries.insert_returning_id(
            INSERT_GAME_SQL,
            (
                game.room_id,
                game.white_player_id,
                game.black_player_id,
                game.winner_id,
                game.result,
                game.white_elo_before,
                game.white_elo_after,
                game.black_elo_before,
                game.black_elo_after,
                _as_timestamp(game.started_at),
                _as_timestamp(game.ended_at),
            ),
            connection=connection,
        )
        if not inserted.is_ok:
            raise RuntimeError(f"Insert of room {game.room_id} failed")
        if inserted.value is not None:
            return SaveOutcome(game_id=inserted.value, already_existed=False)

        existing = await self._queries.fetch_one(
            SELECT_GAME_ID_BY_ROOM_SQL, (game.room_id,),
        )
        if not existing.is_ok or existing.value is None:
            raise RuntimeError(f"Room {game.room_id} conflicted but no row was found")
        return SaveOutcome(game_id=existing.value[0], already_existed=True)

    async def _insert_move_rows(
        self, connection, game_id: int, moves: List[SavableMove]
    ) -> None:
        result = await self._queries.execute_many(
            INSERT_MOVE_SQL,
            [
                (
                    game_id,
                    move.move_number,
                    move.from_square,
                    move.to_square,
                    move.piece_type,
                    move.piece_color,
                    move.captured_piece,
                    move.timestamp,
                )
                for move in moves
            ],
            connection=connection,
        )
        if not result.is_ok:
            raise RuntimeError(f"Move insert for game {game_id} failed")

    async def _recompute_statistics(self, connection, user_id: int) -> None:
        """Rebuild one player's cached aggregates from the games table.

        Runs after the game row within the same transaction, so it folds the
        just-inserted game in.
        """
        result = await self._queries.execute(
            RECOMPUTE_STATISTICS_SQL, (user_id,), connection=connection
        )
        if not result.is_ok:
            raise RuntimeError(f"Statistics recompute for user {user_id} failed")

    async def get_game_statistics(
        self, user_id: int
    ) -> Optional[Tuple[int, int, int, int, Optional[int], Optional[int]]]:
        """Fetch a player's cached aggregates (total, wins, losses, draws, peak, low)."""
        result = await self._queries.fetch_one(SELECT_STATISTICS_SQL, (user_id,))
        if not result.is_ok:
            return None
        return result.value

    async def get_game(self, game_id: int) -> Optional[Tuple]:
        """Fetch one completed game joined to both players' usernames."""
        result = await self._queries.fetch_one(SELECT_GAME_SQL, (game_id,))
        if not result.is_ok:
            return None
        return result.value

    async def get_moves(self, game_id: int) -> List[Tuple]:
        """Fetch a game's resolved moves in play order (by move_number)."""
        result = await self._queries.fetch_all(SELECT_MOVES_SQL, (game_id,))
        return result.value if result.is_ok else []

    async def get_leaderboard(self, limit: int = DEFAULT_LEADERBOARD_LIMIT) -> List[Tuple]:
        """Top players by ELO, restricted to those with at least one game."""
        result = await self._queries.fetch_all(SELECT_LEADERBOARD_SQL, (limit,))
        return result.value if result.is_ok else []


def _as_timestamp(value: datetime) -> str:
    """Render a datetime for a `::timestamptz` cast.

    Bound as text rather than as a `datetime` so the executor's bindable-value
    check stays as narrow as it is — see this module's docstring.
    """
    return value.isoformat()
