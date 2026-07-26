"""Authentication service — handles user registration and login workflows.

Owns: input validation, registration, and login authentication against the Database.
Must not own: network sockets or GUI interfaces.
"""

import logging
from typing import Optional, Tuple

from core.model.game_state import Result
from server.infrastructure.database.database import Database

_LOGGER = logging.getLogger(__name__)

MIN_USERNAME_LEN = 3
MAX_USERNAME_LEN = 32
MIN_PASSWORD_LEN = 4
MAX_PASSWORD_LEN = 128

INVALID_CREDENTIALS_MESSAGE = "Invalid username or password"


class AuthService:
    """Coordinates registration and login using Database.

    Every credential check here is server-side and unconditional: the client is
    an untrusted source, so whatever it validated locally is re-validated (or
    validated for the first time) before a value reaches the database.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def register(self, username: str, password_plain: str) -> Result[Tuple[int, str, int], str]:
        """Register a new user account."""
        username = username.strip()
        credentials = self._check_credential_shape(username, password_plain)
        if not credentials.is_ok:
            return Result.fail(credentials.error)

        user_id = await self._db.create_user(username, password_plain)
        if user_id is None:
            return Result.fail(f"Username '{username}' is already taken")

        user = await self._db.get_user_by_username(username)
        if user is None:
            return Result.fail("User creation failed")

        _LOGGER.info("User registered successfully: %s (id=%d)", username, user_id)
        return Result.ok(user)

    async def login(self, username: str, password_plain: str) -> Result[Tuple[int, str, int], str]:
        """Authenticate an existing user.

        Every refusal answers with the same message. Distinguishing "no such
        user" from "wrong password" — or from a database failure — would let an
        unauthenticated caller enumerate accounts and probe server state.
        """
        username = username.strip()
        if not self._is_within_credential_bounds(username, password_plain):
            return Result.fail(INVALID_CREDENTIALS_MESSAGE)

        auth_result = await self._db.authenticate_user(username, password_plain)
        if auth_result is None:
            return Result.fail(INVALID_CREDENTIALS_MESSAGE)

        _LOGGER.info("User logged in successfully: %s (id=%d)", username, auth_result[0])
        return Result.ok(auth_result)

    @staticmethod
    def _check_credential_shape(username: str, password_plain: str) -> Result[None, str]:
        """Explain what a new account's credentials must look like."""
        if not isinstance(username, str) or not isinstance(password_plain, str):
            return Result.fail("Username and password must be text")
        if len(username) < MIN_USERNAME_LEN:
            return Result.fail(f"Username must be at least {MIN_USERNAME_LEN} characters")
        if len(username) > MAX_USERNAME_LEN:
            return Result.fail(f"Username must be at most {MAX_USERNAME_LEN} characters")
        if len(password_plain) < MIN_PASSWORD_LEN:
            return Result.fail(f"Password must be at least {MIN_PASSWORD_LEN} characters")
        if len(password_plain) > MAX_PASSWORD_LEN:
            return Result.fail(f"Password must be at most {MAX_PASSWORD_LEN} characters")
        return Result.ok(None)

    @classmethod
    def _is_within_credential_bounds(cls, username: str, password_plain: str) -> bool:
        """Reject oversized login input before it reaches bcrypt or the database.

        Login reuses the registration bounds but never reports which one failed,
        so the check stays a plain boolean.
        """
        return cls._check_credential_shape(username, password_plain).is_ok
