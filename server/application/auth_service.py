"""Authentication service — handles user registration and login workflows.

Owns: input validation, registration, and login authentication against the Database.
Must not own: network sockets or GUI interfaces.
"""

import logging
from typing import Optional, Tuple

from shared.model.game_state import Result
from server.infrastructure.database.database import Database

_LOGGER = logging.getLogger(__name__)

MIN_USERNAME_LEN = 3
MIN_PASSWORD_LEN = 4


class AuthService:
    """Coordinates registration and login using Database."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def register(self, username: str, password_plain: str) -> Result[Tuple[int, str, int], str]:
        """Register a new user account."""
        username = username.strip()
        if len(username) < MIN_USERNAME_LEN:
            return Result.fail(f"Username must be at least {MIN_USERNAME_LEN} characters")

        if len(password_plain) < MIN_PASSWORD_LEN:
            return Result.fail(f"Password must be at least {MIN_PASSWORD_LEN} characters")

        user_id = await self._db.create_user(username, password_plain)
        if user_id is None:
            return Result.fail(f"Username '{username}' is already taken")

        user = await self._db.get_user_by_username(username)
        if user is None:
            return Result.fail("User creation failed")

        _LOGGER.info("User registered successfully: %s (id=%d)", username, user_id)
        return Result.ok(user)

    async def login(self, username: str, password_plain: str) -> Result[Tuple[int, str, int], str]:
        """Authenticate an existing user."""
        username = username.strip()
        auth_result = await self._db.authenticate_user(username, password_plain)

        if auth_result is None:
            return Result.fail("Invalid username or password")

        _LOGGER.info("User logged in successfully: %s (id=%d)", username, auth_result[0])
        return Result.ok(auth_result)
