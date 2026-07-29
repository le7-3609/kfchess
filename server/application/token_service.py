"""Session tokens — issued once by the API tier, verified by the WebSocket tier.

Layer: application (server/application)
Owns: minting and verifying the signed tokens that stand in for a password on
every socket after the first login, the key-rotation overlap window, and the
access/refresh distinction.
Must not own: credential checking (AuthService), transport (presentation), or
where the signing key comes from (the entry point reads it from the
environment and hands it in).

Why this exists: without it, every WebSocket connection carries a username and
password and pays for a bcrypt verification. That makes bcrypt's deliberate
slowness an availability problem, and it puts the password on the wire again on
every reconnect — including the reconnect that follows every deploy of the
WebSocket tier. Verifying a signature costs microseconds and needs no user
table at all, which is the property that lets the socket tier be split from the
API tier without a shared database.

The token is a JWT (HS256), written directly rather than through a library: the
format is a signature over two base64url segments, and the alternative is a
runtime dependency for sixty lines of hmac.
"""

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from core.model.game_state import Result

_LOGGER = logging.getLogger(__name__)

DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 3600
DEFAULT_REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 3600

# A little slack for clock skew between the tier that signs and the tier that
# verifies. Without it a token minted on a host whose clock runs slightly ahead
# is rejected as not-yet-valid by a host whose clock runs behind.
CLOCK_SKEW_TOLERANCE_SECONDS = 30

ALGORITHM = "HS256"
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

CLAIM_SUBJECT = "sub"
CLAIM_USERNAME = "username"
CLAIM_ELO = "elo"
CLAIM_TYPE = "typ"
CLAIM_ISSUED_AT = "iat"
CLAIM_EXPIRES_AT = "exp"

_HEADER = {"alg": ALGORITHM, "typ": "JWT"}
_SEGMENT_SEPARATOR = "."
_EXPECTED_SEGMENTS = 3

INVALID_TOKEN_MESSAGE = "Invalid or expired token"


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


@dataclass(frozen=True)
class TokenClaims:
    """The identity a verified token asserts."""

    user_id: int
    username: str
    elo: int
    token_type: str
    issued_at: int
    expires_at: int

    @property
    def is_access_token(self) -> bool:
        return self.token_type == TOKEN_TYPE_ACCESS

    @property
    def is_refresh_token(self) -> bool:
        return self.token_type == TOKEN_TYPE_REFRESH


@dataclass(frozen=True)
class TokenPair:
    """What a successful login hands back: a short access token and a refresh."""

    access_token: str
    refresh_token: str
    expires_in_seconds: int


class TokenService:
    """Signs and verifies identity tokens with a rotatable HMAC key."""

    def __init__(
        self,
        signing_key: str,
        previous_keys: Sequence[str] = (),
        access_ttl_seconds: int = DEFAULT_ACCESS_TOKEN_TTL_SECONDS,
        refresh_ttl_seconds: int = DEFAULT_REFRESH_TOKEN_TTL_SECONDS,
        time_fn=time.time,
    ) -> None:
        if not signing_key:
            raise ValueError("A token signing key is required")
        self._signing_key = signing_key.encode("utf-8")
        # Verification accepts the previous keys as well as the current one, so
        # a key rotation does not invalidate every token in flight at the moment
        # it happens. Signing only ever uses the current key.
        self._verification_keys: List[bytes] = [self._signing_key] + [
            key.encode("utf-8") for key in previous_keys if key
        ]
        self._access_ttl_seconds = access_ttl_seconds
        self._refresh_ttl_seconds = refresh_ttl_seconds
        self._time_fn = time_fn

    @property
    def access_ttl_seconds(self) -> int:
        return self._access_ttl_seconds

    def issue_pair(self, user_id: int, username: str, elo: int) -> TokenPair:
        """Mint the access/refresh pair a successful credential check earns."""
        return TokenPair(
            access_token=self.issue(user_id, username, elo, TOKEN_TYPE_ACCESS),
            refresh_token=self.issue(user_id, username, elo, TOKEN_TYPE_REFRESH),
            expires_in_seconds=self._access_ttl_seconds,
        )

    def issue(self, user_id: int, username: str, elo: int, token_type: str) -> str:
        """Mint one signed token of *token_type* for the given identity."""
        issued_at = int(self._time_fn())
        ttl = (
            self._refresh_ttl_seconds
            if token_type == TOKEN_TYPE_REFRESH
            else self._access_ttl_seconds
        )
        payload = {
            CLAIM_SUBJECT: user_id,
            CLAIM_USERNAME: username,
            CLAIM_ELO: elo,
            CLAIM_TYPE: token_type,
            CLAIM_ISSUED_AT: issued_at,
            CLAIM_EXPIRES_AT: issued_at + ttl,
        }
        signing_input = self._signing_input(_HEADER, payload)
        signature = self._sign(signing_input, self._signing_key)
        return f"{signing_input}{_SEGMENT_SEPARATOR}{_b64url_encode(signature)}"

    def verify(self, token: Any, expected_type: str = TOKEN_TYPE_ACCESS) -> Result[TokenClaims, str]:
        """Check a token's signature, type and expiry.

        Every refusal answers with one identical message. A caller that could
        distinguish "bad signature" from "expired" from "wrong type" learns
        something about a token it does not hold, and none of those distinctions
        help a legitimate client, which reacts to all three by re-authenticating.
        """
        segments = self._split(token)
        if segments is None:
            return Result.fail(INVALID_TOKEN_MESSAGE)

        header_segment, payload_segment, signature_segment = segments
        signing_input = f"{header_segment}{_SEGMENT_SEPARATOR}{payload_segment}"
        if not self._signature_matches(signing_input, signature_segment):
            return Result.fail(INVALID_TOKEN_MESSAGE)

        claims = self._decode_claims(payload_segment)
        if claims is None or claims.token_type != expected_type:
            return Result.fail(INVALID_TOKEN_MESSAGE)
        if claims.expires_at + CLOCK_SKEW_TOLERANCE_SECONDS < int(self._time_fn()):
            return Result.fail(INVALID_TOKEN_MESSAGE)
        return Result.ok(claims)

    @staticmethod
    def _split(token: Any) -> Optional[Sequence[str]]:
        if not isinstance(token, str):
            return None
        segments = token.split(_SEGMENT_SEPARATOR)
        return segments if len(segments) == _EXPECTED_SEGMENTS else None

    def _signature_matches(self, signing_input: str, signature_segment: str) -> bool:
        """Compare against every accepted key in constant time.

        `compare_digest` rather than `==` because a byte-by-byte comparison
        leaks, through its timing, how much of a forged signature was correct —
        which is enough to construct the rest one byte at a time.
        """
        try:
            presented = _b64url_decode(signature_segment)
        except Exception:
            return False
        return any(
            hmac.compare_digest(presented, self._sign(signing_input, key))
            for key in self._verification_keys
        )

    @staticmethod
    def _decode_claims(payload_segment: str) -> Optional[TokenClaims]:
        """Read a verified payload's claims, or None if it is not well-formed.

        Only reached after the signature checks out, so a malformed payload here
        means a token this server itself minted has drifted from this shape —
        still refused rather than trusted.
        """
        try:
            payload = json.loads(_b64url_decode(payload_segment))
            return TokenClaims(
                user_id=int(payload[CLAIM_SUBJECT]),
                username=str(payload[CLAIM_USERNAME]),
                elo=int(payload[CLAIM_ELO]),
                token_type=str(payload[CLAIM_TYPE]),
                issued_at=int(payload[CLAIM_ISSUED_AT]),
                expires_at=int(payload[CLAIM_EXPIRES_AT]),
            )
        except Exception:
            _LOGGER.warning("Discarded a signed token whose payload did not parse")
            return None

    @staticmethod
    def _signing_input(header: Dict[str, Any], payload: Dict[str, Any]) -> str:
        encoded_header = _b64url_encode(
            json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        encoded_payload = _b64url_encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        return f"{encoded_header}{_SEGMENT_SEPARATOR}{encoded_payload}"

    @staticmethod
    def _sign(signing_input: str, key: bytes) -> bytes:
        return hmac.new(key, signing_input.encode("ascii"), hashlib.sha256).digest()
