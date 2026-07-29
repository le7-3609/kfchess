"""SQLite database adapter — user auth, ELO persistence, and completed-game history.

Owns: the persistent relational schema (users, games, moves, game_statistics),
bcrypt password hashing, atomic ELO rating updates, and the single all-or-
nothing transaction that saves a finished game with its moves and refreshed
per-player statistics.
Must not own: game rules, WebSocket sessions, network routing, or the decision
of *which* games are eligible to persist (that gate lives in the application
layer's GamePersistenceService).

Optimization D: Employs a single persistent connection pattern initialized at server startup
with WAL mode enabled for concurrent safety, ensuring clean shutdown without DB locks.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Protocol, Tuple

import aiosqlite
import bcrypt

from core.config.consts import FILE_ENCODING
from server.domain.matchmaking.elo import DEFAULT_PLAYER_ELO
from server.infrastructure.database.query_executor import SecureQueryExecutor

_LOGGER = logging.getLogger(__name__)

DEFAULT_DB_PATH = "kfchess.db"
DEFAULT_LEADERBOARD_LIMIT = 100
# The same number, enforced as a ceiling rather than only a default: a caller
# that asks for a larger page must be clamped, not obeyed, or one request can
# make the server read and serialize the whole users table.
MAX_LEADERBOARD_LIMIT = DEFAULT_LEADERBOARD_LIMIT


@dataclass(frozen=True)
class SaveOutcome:
    """What a completed-game save did: which row it resolved to, and whether it
    was already there.

    The distinction matters to the caller. A save that found the room already
    persisted has succeeded — the game is in the database, which is all the
    caller wanted — while a save that failed has not. Returning a bare id for
    both, or `None` for both, collapses those into one indistinguishable answer
    and makes an at-least-once persistence pipeline impossible to reason about.
    """

    game_id: int
    already_existed: bool


class SavableMove(Protocol):
    """The move shape this adapter persists — application's PersistedMove fits it.

    Declared here, in the layer that consumes it, so the dependency arrow stays
    infrastructure <- application: the caller's DTO satisfies this structurally
    rather than this module reaching outward to import it.
    """

    move_number: int
    from_square: str
    to_square: str
    piece_type: str
    piece_color: str
    captured_piece: Optional[str]
    timestamp: float


class SavableGame(Protocol):
    """The completed-game shape this adapter persists — application's GameResult fits it."""

    room_id: str
    white_player_id: int
    black_player_id: int
    winner_id: Optional[int]
    result: str
    white_elo_before: int
    white_elo_after: int
    black_elo_before: int
    black_elo_after: int
    started_at: datetime
    ended_at: datetime


CREATE_USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    elo INTEGER NOT NULL DEFAULT 1200,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# Completed games only — active rooms live in memory in RoomManager. winner_id
# is NULL for a draw; result stores the terminal reason (checkmate, stalemate,
# timeout, ...). room_id is UNIQUE so the same room can never be saved twice.
CREATE_GAMES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT UNIQUE NOT NULL,
    white_player_id INTEGER NOT NULL,
    black_player_id INTEGER NOT NULL,
    winner_id INTEGER,
    result TEXT NOT NULL,
    white_elo_before INTEGER NOT NULL,
    white_elo_after INTEGER NOT NULL,
    black_elo_before INTEGER NOT NULL,
    black_elo_after INTEGER NOT NULL,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (white_player_id) REFERENCES users(id),
    FOREIGN KEY (black_player_id) REFERENCES users(id),
    FOREIGN KEY (winner_id) REFERENCES users(id)
);
"""

# One row per resolved move. timestamp is the game-clock instant the move
# landed (ms elapsed), taken straight from MovesLog, so replay timing survives.
CREATE_MOVES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id INTEGER NOT NULL,
    move_number INTEGER NOT NULL,
    from_square TEXT NOT NULL,
    to_square TEXT NOT NULL,
    piece_type TEXT NOT NULL,
    piece_color TEXT NOT NULL,
    captured_piece TEXT,
    timestamp REAL NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE
);
"""

# Denormalized per-player cache, always recomputed from the games table inside
# the same transaction that inserts a game — never incremented in place, so it
# can never drift from the source of truth.
CREATE_GAME_STATISTICS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS game_statistics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    total_games INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    draws INTEGER DEFAULT 0,
    elo_peak INTEGER,
    elo_low INTEGER,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
"""

CREATE_INDEXES_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_games_white_player ON games(white_player_id);",
    "CREATE INDEX IF NOT EXISTS idx_games_black_player ON games(black_player_id);",
    "CREATE INDEX IF NOT EXISTS idx_games_started_at ON games(started_at);",
    "CREATE INDEX IF NOT EXISTS idx_moves_game_id ON moves(game_id);",
    "CREATE INDEX IF NOT EXISTS idx_moves_created_at ON moves(created_at);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_game_statistics_user_id ON game_statistics(user_id);",
    # Leaderboard orders by ELO descending; the index keeps top-N cheap.
    "CREATE INDEX IF NOT EXISTS idx_users_elo ON users(elo);",
)


# The statements reached by client-supplied values. They are module constants
# rather than call-site strings so there is no expression at the call site an
# f-string or concatenation could ever be added to: the SQL text is fixed at
# import time and user input can only arrive through the `?` placeholders.
INSERT_USER_SQL = "INSERT INTO users (username, password_hash, elo) VALUES (?, ?, ?)"

SELECT_USER_CREDENTIALS_SQL = (
    "SELECT id, username, password_hash, elo FROM users WHERE username = ?"
)

SELECT_USER_PROFILE_SQL = "SELECT id, username, elo FROM users WHERE username = ?"

UPDATE_USER_ELO_SQL = "UPDATE users SET elo = ? WHERE username = ?"

UPDATE_PASSWORD_HASH_SQL = "UPDATE users SET password_hash = ? WHERE username = ?"

SELECT_GAME_ID_BY_ROOM_SQL = "SELECT id FROM games WHERE room_id = ?"

# The cheapest statement that proves the connection can still round-trip, used
# by the readiness probe. Deliberately touches no table: readiness asks whether
# this replica can reach its database, not whether the schema is populated.
PING_SQL = "SELECT 1"

# Pinned rather than left to `bcrypt.gensalt()`'s library default, which has
# moved between releases: the work factor protecting every password in this
# database must be a deliberate, reviewable number, not a property of whichever
# bcrypt version the image happened to resolve.
BCRYPT_COST_FACTOR = 12

# A bcrypt hash names its own cost in the third `$`-delimited field
# ("$2b$12$..."), which is what makes rehash-on-login possible without storing
# the cost separately.
_BCRYPT_COST_PATTERN = re.compile(r"^\$2[aby]?\$(\d{2})\$")


class Database:
    """Async SQLite persistence adapter."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        # Bound to the accessor, not the connection object, so the executor
        # always sees the currently open connection and fails loudly if closed.
        self._queries = SecureQueryExecutor(self._require_connection)

    async def connect(self) -> None:
        """Open persistent SQLite connection and initialize schema."""
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._db_path)
        # Optimization D: WAL mode for concurrent read safety & reliability
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        # ON DELETE CASCADE on moves/game_statistics only fires with FK
        # enforcement on — SQLite leaves it off per-connection by default.
        await self._conn.execute("PRAGMA foreign_keys=ON;")
        await self._conn.execute(CREATE_USERS_TABLE_SQL)
        await self._conn.execute(CREATE_GAMES_TABLE_SQL)
        await self._conn.execute(CREATE_MOVES_TABLE_SQL)
        await self._conn.execute(CREATE_GAME_STATISTICS_TABLE_SQL)
        for index_sql in CREATE_INDEXES_SQL:
            await self._conn.execute(index_sql)
        await self._conn.commit()
        _LOGGER.info("Database connected at %s (WAL mode enabled)", self._db_path)

    async def close(self) -> None:
        """Gracefully close the database connection."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            _LOGGER.info("Database connection closed")

    def _require_connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database connection is not open. Call connect() first.")
        return self._conn

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    async def ping(self) -> bool:
        """Report whether the connection can still answer a trivial query.

        Readiness (not liveness) depends on this: a replica that cannot reach
        its database should stop being handed new work, while the games already
        running on it continue.
        """
        if self._conn is None:
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
        pw_hash = await self._hash_password(password_plain)

        insert = await self._queries.insert_returning_id(
            INSERT_USER_SQL,
            (username, pw_hash, initial_elo),
        )
        if not insert.is_ok:
            return None
        if insert.value is None:
            _LOGGER.warning("Attempted duplicate user registration: %s", username)
        return insert.value

    async def authenticate_user(self, username: str, password_plain: str) -> Optional[Tuple[int, str, int]]:
        """Verify username and password.

        The username is bound as a parameter, so a value like
        ``' OR '1'='1`` is compared as a literal name and matches nothing.

        Returns:
            Tuple of (user_id, username, elo) if authenticated, or None. A
            lookup failure also yields None: an unavailable database must read
            as "not authenticated" to the caller rather than surfacing why.
        """
        lookup = await self._queries.fetch_one(SELECT_USER_CREDENTIALS_SQL, (username,))
        if not lookup.is_ok or lookup.value is None:
            return None

        user_id, name, pw_hash, elo = lookup.value
        if not await self._verify_password(password_plain, pw_hash):
            return None

        await self._rehash_if_below_target_cost(name, pw_hash, password_plain)
        return (user_id, name, elo)

    async def _rehash_if_below_target_cost(
        self, username: str, stored_hash: str, password_plain: str
    ) -> None:
        """Upgrade a hash that predates the current cost factor, on successful login.

        Without this the cost can only ever apply to accounts created after it
        was raised, and every existing password stays at whatever factor was in
        force when it was set. Login is the only moment the plaintext is
        available to rehash from, so it is the only place this can happen.

        A failure to write the new hash is deliberately not fatal: the login
        itself already succeeded against a valid hash, and refusing it over a
        failed optimisation would turn a stale cost factor into an outage.
        """
        if self._cost_of(stored_hash) >= BCRYPT_COST_FACTOR:
            return

        upgraded = await self._hash_password(password_plain)
        update = await self._queries.execute(UPDATE_PASSWORD_HASH_SQL, (upgraded, username))
        if update.is_ok:
            _LOGGER.info("Rehashed %s's password at cost %d", username, BCRYPT_COST_FACTOR)

    @staticmethod
    def _cost_of(stored_hash: str) -> int:
        """The work factor encoded in a bcrypt hash, or 0 if unreadable.

        An unreadable hash reports 0 so it sorts below any target and gets
        rehashed on the next successful login.
        """
        match = _BCRYPT_COST_PATTERN.match(stored_hash or "")
        return int(match.group(1)) if match else 0

    @staticmethod
    async def _hash_password(password_plain: str) -> str:
        """Hash a password on a worker thread.

        bcrypt is synchronous CPU work by design, and at cost 12 it occupies a
        core for on the order of a quarter of a second. Running it inline would
        block the same event loop that drives every room's AsyncGameRunner —
        five missed ticks in every game on this process, per login. Off-loading
        it is a correctness fix for the simulation, not only a throughput fix
        for auth.
        """
        return await asyncio.to_thread(Database._hash_password_blocking, password_plain)

    @staticmethod
    def _hash_password_blocking(password_plain: str) -> str:
        salt = bcrypt.gensalt(rounds=BCRYPT_COST_FACTOR)
        return bcrypt.hashpw(password_plain.encode(FILE_ENCODING), salt).decode(FILE_ENCODING)

    @staticmethod
    async def _verify_password(password_plain: str, stored_hash: str) -> bool:
        """Check a password on a worker thread, for the same reason as hashing."""
        return await asyncio.to_thread(
            Database._verify_password_blocking, password_plain, stored_hash
        )

    @staticmethod
    def _verify_password_blocking(password_plain: str, stored_hash: str) -> bool:
        try:
            return bcrypt.checkpw(
                password_plain.encode(FILE_ENCODING), stored_hash.encode(FILE_ENCODING)
            )
        except ValueError:
            # A stored value that is not a bcrypt hash at all cannot match any
            # password; it must read as a failed login rather than an exception
            # that escapes into the auth path.
            _LOGGER.error("Stored credential is not a valid bcrypt hash")
            return False

    async def get_user_by_username(self, username: str) -> Optional[Tuple[int, str, int]]:
        """Fetch user profile (user_id, username, elo)."""
        lookup = await self._queries.fetch_one(SELECT_USER_PROFILE_SQL, (username,))
        if not lookup.is_ok or lookup.value is None:
            return None
        user_id, name, elo = lookup.value
        return (user_id, name, elo)

    async def update_elo(self, username: str, new_elo: int) -> bool:
        """Update a user's ELO rating atomically."""
        update = await self._queries.execute(UPDATE_USER_ELO_SQL, (new_elo, username))
        if not update.is_ok:
            return False
        rowcount, _ = update.value
        return rowcount > 0

    async def save_completed_game(
        self, game: SavableGame, moves: List[SavableMove]
    ) -> Optional[SaveOutcome]:
        """Persist a finished game, its moves, and both players' refreshed stats.

        The whole operation is a single transaction: the game row, every move
        row, and the recomputed statistics for both players either all land or
        none do. A bad foreign key or any other failure rolls the entire batch
        back and returns None rather than leaving a game with no moves or stale
        statistics.

        Saving the *same* room twice is not a failure. A natural game end and a
        disconnect forfeit can race the same room, and a redelivered persistence
        event will do the same once that work moves onto a durable stream. The
        insert therefore does nothing on a `room_id` conflict and the existing
        id is returned, flagged as pre-existing — so the caller can tell "already
        saved" (success) from "could not save" (None), which the previous
        undifferentiated `None` made impossible.

        Returns:
            A SaveOutcome naming the game's id, or None if the save was rolled
            back.
        """
        conn = self._require_connection()
        try:
            outcome = await self._insert_game_row(conn, game)
            if outcome.already_existed:
                await conn.commit()
                _LOGGER.info(
                    "Room %s is already persisted as game %s; save was a no-op",
                    game.room_id, outcome.game_id,
                )
                return outcome

            await self._insert_move_rows(conn, outcome.game_id, moves)
            await self._recompute_statistics(conn, game.white_player_id)
            await self._recompute_statistics(conn, game.black_player_id)
            await conn.commit()
            _LOGGER.info(
                "Persisted game %s (room %s) with %d moves",
                outcome.game_id, game.room_id, len(moves),
            )
            return outcome
        except Exception as exc:
            await conn.rollback()
            _LOGGER.exception("Rolled back save of room %s: %s", game.room_id, exc)
            return None

    @staticmethod
    async def _insert_game_row(conn: aiosqlite.Connection, game: SavableGame) -> SaveOutcome:
        """Insert the game row, or resolve the id of the one already there.

        `ON CONFLICT DO NOTHING` rather than `RETURNING id`: RETURNING needs
        SQLite 3.35+, which the interpreter's bundled library does not
        guarantee, and the follow-up lookup is only reached on the rare
        duplicate path anyway.
        """
        cursor = await conn.execute(
            """
            INSERT INTO games (
                room_id, white_player_id, black_player_id, winner_id, result,
                white_elo_before, white_elo_after, black_elo_before, black_elo_after,
                started_at, ended_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (room_id) DO NOTHING
            """,
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
                game.started_at.isoformat(),
                game.ended_at.isoformat(),
            ),
        )
        if cursor.rowcount:
            return SaveOutcome(game_id=cursor.lastrowid, already_existed=False)

        async with conn.execute(SELECT_GAME_ID_BY_ROOM_SQL, (game.room_id,)) as existing:
            row = await existing.fetchone()
        if row is None:
            raise RuntimeError(f"Room {game.room_id} conflicted but no row was found")
        return SaveOutcome(game_id=row[0], already_existed=True)

    @staticmethod
    async def _insert_move_rows(
        conn: aiosqlite.Connection, game_id: int, moves: List[SavableMove]
    ) -> None:
        await conn.executemany(
            """
            INSERT INTO moves (
                game_id, move_number, from_square, to_square,
                piece_type, piece_color, captured_piece, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
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
        )

    @staticmethod
    async def _recompute_statistics(conn: aiosqlite.Connection, user_id: int) -> None:
        """Rebuild one player's cached aggregates from the games table.

        Recomputing from source (rather than incrementing a counter) keeps the
        cache correct even if a game is ever re-saved or deleted, and folds the
        just-inserted game in because it runs after the game row within the same
        transaction.
        """
        async with conn.execute(
            """
            SELECT
                COUNT(*) AS total_games,
                SUM(CASE WHEN winner_id = :uid THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN winner_id IS NOT NULL AND winner_id != :uid THEN 1 ELSE 0 END) AS losses,
                SUM(CASE WHEN winner_id IS NULL THEN 1 ELSE 0 END) AS draws,
                MAX(CASE WHEN white_player_id = :uid THEN white_elo_after ELSE black_elo_after END) AS elo_peak,
                MIN(CASE WHEN white_player_id = :uid THEN white_elo_after ELSE black_elo_after END) AS elo_low
            FROM games
            WHERE white_player_id = :uid OR black_player_id = :uid
            """,
            {"uid": user_id},
        ) as cursor:
            row = await cursor.fetchone()

        total_games, wins, losses, draws, elo_peak, elo_low = row

        await conn.execute(
            """
            INSERT INTO game_statistics (
                user_id, total_games, wins, losses, draws, elo_peak, elo_low, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET
                total_games = excluded.total_games,
                wins = excluded.wins,
                losses = excluded.losses,
                draws = excluded.draws,
                elo_peak = excluded.elo_peak,
                elo_low = excluded.elo_low,
                updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, total_games, wins or 0, losses or 0, draws or 0, elo_peak, elo_low),
        )

    async def get_game_statistics(self, user_id: int) -> Optional[Tuple[int, int, int, int, Optional[int], Optional[int]]]:
        """Fetch a player's cached aggregates (total, wins, losses, draws, peak, low)."""
        conn = self._require_connection()
        async with conn.execute(
            "SELECT total_games, wins, losses, draws, elo_peak, elo_low "
            "FROM game_statistics WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return tuple(row) if row is not None else None

    async def get_game(self, game_id: int) -> Optional[Tuple]:
        """Fetch one completed game joined to both players' usernames.

        Returns the game row followed by white_username and black_username, so
        a replay or PGN reader never has to resolve player ids separately.
        Returns None if no game has that id.
        """
        conn = self._require_connection()
        async with conn.execute(
            """
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
            WHERE g.id = ?
            """,
            (game_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return tuple(row) if row is not None else None

    async def get_moves(self, game_id: int) -> List[Tuple]:
        """Fetch a game's resolved moves in play order (by move_number)."""
        conn = self._require_connection()
        async with conn.execute(
            """
            SELECT move_number, from_square, to_square,
                   piece_type, piece_color, captured_piece, timestamp
            FROM moves
            WHERE game_id = ?
            ORDER BY move_number
            """,
            (game_id,),
        ) as cursor:
            return [tuple(row) for row in await cursor.fetchall()]

    async def get_leaderboard(self, limit: int = DEFAULT_LEADERBOARD_LIMIT) -> List[Tuple]:
        """Top players by ELO, restricted to those with at least one game.

        The JOIN (not LEFT JOIN) excludes freshly-registered users who have
        never finished a game, matching what a leaderboard should show.
        """
        conn = self._require_connection()
        async with conn.execute(
            """
            SELECT u.username, u.elo, s.total_games, s.wins
            FROM users u
            JOIN game_statistics s ON s.user_id = u.id
            ORDER BY u.elo DESC, u.username ASC
            LIMIT ?
            """,
            (limit,),
        ) as cursor:
            return [tuple(row) for row in await cursor.fetchall()]
