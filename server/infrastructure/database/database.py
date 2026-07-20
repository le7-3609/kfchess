"""SQLite database adapter — handles user authentication, passwords, and ELO persistence.

Owns: persistent user table, bcrypt password hashing, and atomic ELO rating updates.
Must not own: game rules, WebSocket sessions, or network routing.

Optimization D: Employs a single persistent connection pattern initialized at server startup
with WAL mode enabled for concurrent safety, ensuring clean shutdown without DB locks.
"""

import logging
from typing import Optional, Tuple

import aiosqlite
import bcrypt

_LOGGER = logging.getLogger(__name__)

CREATE_USERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    elo INTEGER NOT NULL DEFAULT 1200,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    """Async SQLite persistence adapter."""

    def __init__(self, db_path: str = "kfchess.db") -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        """Open persistent SQLite connection and initialize schema."""
        if self._conn is not None:
            return
        self._conn = await aiosqlite.connect(self._db_path)
        # Optimization D: WAL mode for concurrent read safety & reliability
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.execute(CREATE_USERS_TABLE_SQL)
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

    async def create_user(self, username: str, password_plain: str, initial_elo: int = 1200) -> Optional[int]:
        """Hash password with bcrypt and insert a new user.

        Returns:
            user_id of created user, or None if username already exists.
        """
        conn = self._require_connection()
        pw_bytes = password_plain.encode("utf-8")
        salt = bcrypt.gensalt()
        pw_hash = bcrypt.hashpw(pw_bytes, salt).decode("utf-8")

        try:
            cursor = await conn.execute(
                "INSERT INTO users (username, password_hash, elo) VALUES (?, ?, ?)",
                (username, pw_hash, initial_elo),
            )
            await conn.commit()
            return cursor.lastrowid
        except aiosqlite.IntegrityError:
            _LOGGER.warning("Attempted duplicate user registration: %s", username)
            return None

    async def authenticate_user(self, username: str, password_plain: str) -> Optional[Tuple[int, str, int]]:
        """Verify username and password.

        Returns:
            Tuple of (user_id, username, elo) if authenticated, or None.
        """
        conn = self._require_connection()
        async with conn.execute(
            "SELECT id, username, password_hash, elo FROM users WHERE username = ?",
            (username,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None

            user_id, name, pw_hash, elo = row
            pw_bytes = password_plain.encode("utf-8")
            hash_bytes = pw_hash.encode("utf-8")

            if bcrypt.checkpw(pw_bytes, hash_bytes):
                return (user_id, name, elo)
            return None

    async def get_user_by_username(self, username: str) -> Optional[Tuple[int, str, int]]:
        """Fetch user profile (user_id, username, elo)."""
        conn = self._require_connection()
        async with conn.execute(
            "SELECT id, username, elo FROM users WHERE username = ?",
            (username,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                return None
            return (row[0], row[1], row[2])

    async def update_elo(self, username: str, new_elo: int) -> bool:
        """Update a user's ELO rating atomically."""
        conn = self._require_connection()
        cursor = await conn.execute(
            "UPDATE users SET elo = ? WHERE username = ?",
            (new_elo, username),
        )
        await conn.commit()
        return cursor.rowcount > 0
