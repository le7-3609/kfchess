"""Authentication use case — validates and dispatches one auth attempt.

Layer: application (server/application)
Owns: the shape a valid auth frame must have, the routing of register vs login
onto AuthService, and the cheaper token path that supersedes both once the HTTP
tier has issued one.
Must not own: socket reads, the retry budget, or error framing — the attempt
loop is transport-shaped and stays in the presentation layer, which calls this
once per attempt and turns a failed Result into an `error` frame.

A frame carrying a `token` is resolved by verifying a signature and reading the
claims, with **no user-table read at all**. That is what allows the WebSocket
tier to be deployed and scaled separately from the tier that owns user data
(Server_Design.md, Step 4): once tokens are in use, this side of the split
needs no database.
"""

from typing import Any, Dict, Optional

from core.model.game_state import Result

from server.application.dtos import Identity
from server.application.dtos.frame_fields import (
    FIELD_ACTION,
    FIELD_PASSWORD,
    FIELD_TOKEN,
    FIELD_USERNAME,
)
from server.application.auth_service import AuthService
from server.application.token_service import TokenService

AUTH_ACTION_LOGIN = "login"
AUTH_ACTION_REGISTER = "register"


class AuthUseCase:
    """Resolves one authentication frame into a verified identity."""

    def __init__(
        self,
        auth_service: Optional[AuthService],
        token_service: Optional[TokenService] = None,
    ) -> None:
        self._auth_service = auth_service
        self._token_service = token_service

    @staticmethod
    def is_token_attempt(frame: Dict[str, Any]) -> bool:
        """Report whether *frame* presents a token rather than a password.

        The presentation layer asks so it can skip the per-username failure
        backoff, which exists to slow password guessing and has nothing to
        useful to say about a signature that either verifies or does not.
        """
        return bool(frame.get(FIELD_TOKEN))

    async def authenticate(self, frame: Dict[str, Any]) -> Result[Identity, str]:
        """Validate *frame* and resolve it to an identity, or explain the refusal.

        Returns a failed Result rather than raising so the caller can answer a
        bad attempt and keep the socket open for the next one.
        """
        if self.is_token_attempt(frame):
            return self._authenticate_token(frame[FIELD_TOKEN])
        return await self._authenticate_credentials(frame)

    def _authenticate_token(self, token: Any) -> Result[Identity, str]:
        if self._token_service is None:
            return Result.fail("Token authentication is not configured")

        verified = self._token_service.verify(token)
        if not verified.is_ok:
            return Result.fail(verified.error)

        claims = verified.value
        return Result.ok((claims.user_id, claims.username, claims.elo))

    async def _authenticate_credentials(self, frame: Dict[str, Any]) -> Result[Identity, str]:
        if self._auth_service is None:
            return Result.fail("Server authentication is not configured")

        username = frame.get(FIELD_USERNAME)
        password = frame.get(FIELD_PASSWORD)
        if not username or not password:
            return Result.fail("Auth message requires 'username' and 'password'")

        action = frame.get(FIELD_ACTION)
        if action == AUTH_ACTION_REGISTER:
            return await self._auth_service.register(username, password)
        if action == AUTH_ACTION_LOGIN:
            return await self._auth_service.login(username, password)
        return Result.fail(f"Unknown auth action: {action!r}")
