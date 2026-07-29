"""Unit tests for signed session tokens.

These tokens replace a password on every socket after the first, so the tests
that matter are the ones a forged or stale token would exploit: a tampered
payload, a signature made with the wrong key, an expired token, and a refresh
token presented where an access token is required.
"""

import base64
import json

import pytest

from server.application.token_service import (
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    TokenService,
)

_KEY = "test-signing-key"
_OTHER_KEY = "a-different-signing-key"


class _Clock:
    """A hand-cranked clock, so expiry is asserted rather than waited for."""

    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _service(clock=None, **kwargs) -> TokenService:
    return TokenService(signing_key=_KEY, time_fn=clock or _Clock(), **kwargs)


def test_a_service_without_a_key_refuses_to_exist():
    """Signing with an empty key is signing with a key every attacker has."""
    with pytest.raises(ValueError):
        TokenService(signing_key="")


def test_an_issued_access_token_verifies_and_carries_the_identity():
    service = _service()

    token = service.issue(42, "Alice", 1310, TOKEN_TYPE_ACCESS)
    verified = service.verify(token)

    assert verified.is_ok
    assert verified.value.user_id == 42
    assert verified.value.username == "Alice"
    assert verified.value.elo == 1310
    assert verified.value.is_access_token


def test_issue_pair_returns_an_access_and_a_refresh_token():
    service = _service()

    pair = service.issue_pair(7, "Bob", 1200)

    assert service.verify(pair.access_token, TOKEN_TYPE_ACCESS).is_ok
    assert service.verify(pair.refresh_token, TOKEN_TYPE_REFRESH).is_ok
    assert pair.expires_in_seconds == service.access_ttl_seconds


def test_a_refresh_token_is_not_accepted_where_an_access_token_is_required():
    """A refresh token lives for a month. Accepting one as an access token
    would silently give month-long sessions to a socket tier that expects
    hour-long ones."""
    service = _service()

    pair = service.issue_pair(7, "Bob", 1200)

    assert not service.verify(pair.refresh_token, TOKEN_TYPE_ACCESS).is_ok


def test_a_tampered_payload_is_refused():
    """The signature covers the payload, so editing the claims invalidates it —
    which is the entire reason the socket tier can trust a token without
    reading the user table."""
    service = _service()
    header, payload, signature = service.issue(1, "Alice", 1200, TOKEN_TYPE_ACCESS).split(".")

    claims = json.loads(_b64url_decode(payload))
    claims["sub"] = 999
    forged_payload = _b64url_encode(json.dumps(claims, separators=(",", ":")).encode())

    assert not service.verify(f"{header}.{forged_payload}.{signature}").is_ok


def test_a_token_signed_with_another_key_is_refused():
    foreign = TokenService(signing_key=_OTHER_KEY)

    token = foreign.issue(1, "Alice", 1200, TOKEN_TYPE_ACCESS)

    assert not _service().verify(token).is_ok


def test_an_expired_token_is_refused():
    clock = _Clock()
    service = _service(clock, access_ttl_seconds=60)
    token = service.issue(1, "Alice", 1200, TOKEN_TYPE_ACCESS)

    clock.now += 60 + 31  # past the TTL and past the skew tolerance
    assert not service.verify(token).is_ok


def test_a_token_just_past_expiry_survives_clock_skew():
    """The tier that signs and the tier that verifies are different hosts. A
    token must not be rejected because one of their clocks runs a second fast."""
    clock = _Clock()
    service = _service(clock, access_ttl_seconds=60)
    token = service.issue(1, "Alice", 1200, TOKEN_TYPE_ACCESS)

    clock.now += 61
    assert service.verify(token).is_ok


def test_rotation_keeps_tokens_issued_under_the_previous_key_valid():
    """A rotation with no overlap invalidates every token in flight at the
    moment it happens, which logs out the entire player base at once."""
    old_service = TokenService(signing_key=_OTHER_KEY)
    token = old_service.issue(1, "Alice", 1200, TOKEN_TYPE_ACCESS)

    rotated = TokenService(signing_key=_KEY, previous_keys=[_OTHER_KEY])

    assert rotated.verify(token).is_ok
    # New tokens are signed with the current key only.
    assert not old_service.verify(rotated.issue(1, "Alice", 1200, TOKEN_TYPE_ACCESS)).is_ok


@pytest.mark.parametrize(
    "malformed",
    [None, "", "not-a-token", "only.two", "a.b.c.d", 12345, {"token": "x"}],
)
def test_malformed_input_is_refused_without_raising(malformed):
    """A token arrives straight off the wire, so anything at all can be in this
    field; none of it may reach an exception handler as a crash."""
    assert not _service().verify(malformed).is_ok


def test_every_refusal_answers_with_the_same_message():
    """Distinguishing 'expired' from 'bad signature' tells a caller something
    about a token it does not hold, and helps no legitimate client — all three
    are fixed by re-authenticating."""
    service = _service()
    expired_clock = _Clock()
    short_lived = _service(expired_clock, access_ttl_seconds=1)
    token = short_lived.issue(1, "Alice", 1200, TOKEN_TYPE_ACCESS)
    expired_clock.now += 1000

    messages = {
        service.verify("garbage").error,
        service.verify(TokenService(signing_key=_OTHER_KEY).issue(1, "A", 1, TOKEN_TYPE_ACCESS)).error,
        short_lived.verify(token).error,
    }

    assert len(messages) == 1


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
