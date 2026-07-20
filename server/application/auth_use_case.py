"""Authentication use case — validates and dispatches one auth attempt.

Layer: application (server/application)
Owns: the shape a valid auth frame must have and the routing of register vs
login onto AuthService.
Must not own: socket reads, the retry budget, or error framing — the attempt
loop is transport-shaped and stays in the presentation layer, which calls this
once per attempt and turns a failed Result into an `error` frame.
"""

from typing import Any, Dict, Optional

from shared.model.game_state import Result

from server.application.dtos import Identity
from server.application.auth_service import AuthService

AUTH_ACTION_LOGIN = "login"
AUTH_ACTION_REGISTER = "register"


class AuthUseCase:
    """Resolves one authentication frame into a verified identity."""

    def __init__(self, auth_service: Optional[AuthService]) -> None:
        self._auth_service = auth_service

    async def authenticate(self, frame: Dict[str, Any]) -> Result[Identity, str]:
        """Validate *frame* and resolve it to an identity, or explain the refusal.

        Returns a failed Result rather than raising so the caller can answer a
        bad attempt and keep the socket open for the next one.
        """
        if self._auth_service is None:
            return Result.fail("Server authentication is not configured")

        username = frame.get("username")
        password = frame.get("password")
        if not username or not password:
            return Result.fail("Auth message requires 'username' and 'password'")

        action = frame.get("action")
        if action == AUTH_ACTION_REGISTER:
            return await self._auth_service.register(username, password)
        if action == AUTH_ACTION_LOGIN:
            return await self._auth_service.login(username, password)
        return Result.fail(f"Unknown auth action: {action!r}")
