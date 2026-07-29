"""Edge admission control for the WebSocket tier.

Layer: presentation (server/presentation)
Owns: the four socket-side budgets — new connections per source address,
frames per session, room creations per user, and the backoff a repeatedly
failing login earns — plus resolving which address a connection is keyed by.
Must not own: the counting primitives (infrastructure/services/rate_limiter),
what a refusal looks like on the wire (ws_server frames it), or any game state.

Why these four specifically: every one of them is unbounded in the server this
replaces. `_authenticate` allows three attempts *per connection* and connections
are unlimited, so one host can spend the whole process's CPU on bcrypt;
`_process_message` dispatches every frame that arrives; `_handle_create_room` is
uncapped per user. A budget that is generous relative to real play still turns
each of those from unbounded into arithmetic.
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from server.infrastructure.services.rate_limiter import (
    ExponentialBackoffLimiter,
    FixedWindowCounter,
    KeyedTokenBuckets,
    resolve_client_ip,
)

_LOGGER = logging.getLogger(__name__)

# Sized well above real play. A human generates a few frames a second at most;
# the burst allowance covers a flurry of clicks and a reconnect's catch-up.
DEFAULT_FRAMES_PER_SECOND = 20.0
DEFAULT_FRAME_BURST = 60.0

DEFAULT_CONNECTIONS_PER_MINUTE = 30
DEFAULT_ROOMS_PER_MINUTE = 10
DEFAULT_CONCURRENT_ROOMS_PER_USER = 3

_WINDOW_SECONDS = 60.0
_UNKNOWN_ADDRESS = "unknown"

# Probed across websockets releases: 12 exposes `request_headers` on the
# connection, 14 moved them behind `request.headers`.
_ATTR_REQUEST_HEADERS = "request_headers"
_ATTR_REQUEST = "request"
_ATTR_HEADERS = "headers"
_ATTR_REMOTE_ADDRESS = "remote_address"

HEADER_FORWARDED_FOR = "X-Forwarded-For"


@dataclass(frozen=True)
class AdmissionDecision:
    """Whether an action is permitted, and what to tell the client if not."""

    allowed: bool
    reason: Optional[str] = None

    @staticmethod
    def permit() -> "AdmissionDecision":
        return AdmissionDecision(allowed=True)

    @staticmethod
    def refuse(reason: str) -> "AdmissionDecision":
        return AdmissionDecision(allowed=False, reason=reason)


class ConnectionGuard:
    """Applies the socket tier's admission budgets."""

    def __init__(
        self,
        trusted_proxies: Sequence[str] = (),
        connections_per_minute: int = DEFAULT_CONNECTIONS_PER_MINUTE,
        frames_per_second: float = DEFAULT_FRAMES_PER_SECOND,
        frame_burst: float = DEFAULT_FRAME_BURST,
        rooms_per_minute: int = DEFAULT_ROOMS_PER_MINUTE,
        concurrent_rooms_per_user: int = DEFAULT_CONCURRENT_ROOMS_PER_USER,
    ) -> None:
        self._trusted_proxies = frozenset(trusted_proxies)
        self._connection_limits = FixedWindowCounter(connections_per_minute, _WINDOW_SECONDS)
        self._frame_limits = KeyedTokenBuckets(frames_per_second, frame_burst)
        self._room_limits = FixedWindowCounter(rooms_per_minute, _WINDOW_SECONDS)
        self._concurrent_rooms_per_user = concurrent_rooms_per_user
        self._login_backoff = ExponentialBackoffLimiter()

    @property
    def concurrent_rooms_per_user(self) -> int:
        return self._concurrent_rooms_per_user

    def client_address(self, websocket: Any) -> str:
        """The address this connection is keyed by, honouring a trusted ingress.

        `X-Forwarded-For` is read only when the peer is a configured trusted
        proxy. Trusting it from any peer would make every per-IP budget here
        bypassable with one header.
        """
        peer_ip = self._peer_ip(websocket)
        forwarded = self._forwarded_for(websocket)
        resolved = resolve_client_ip(
            peer_ip, forwarded, peer_is_trusted_proxy=peer_ip in self._trusted_proxies
        )
        return resolved or _UNKNOWN_ADDRESS

    def admit_connection(self, address: str) -> AdmissionDecision:
        """Budget new connections from one source address."""
        if self._connection_limits.allow(address):
            return AdmissionDecision.permit()
        _LOGGER.warning("Refused a connection from %s: connection rate limit", address)
        return AdmissionDecision.refuse("Too many connections; try again shortly")

    def admit_frame(self, session_key: str) -> AdmissionDecision:
        """Budget inbound frames on one socket."""
        if self._frame_limits.allow(session_key):
            return AdmissionDecision.permit()
        return AdmissionDecision.refuse("Message rate limit exceeded")

    def forget_session(self, session_key: str) -> None:
        """Drop a closed socket's bucket so the map does not grow without bound."""
        self._frame_limits.forget(session_key)

    def admit_room_creation(self, username: str, current_room_count: int) -> AdmissionDecision:
        """Budget room creation per user, by both rate and concurrency.

        Two limits rather than one because they answer different abuses: the
        rate cap stops a loop allocating ids as fast as the loop turns, and the
        concurrency cap stops a user parking an unbounded number of live rooms
        that each hold an engine and a tick loop.
        """
        if current_room_count >= self._concurrent_rooms_per_user:
            return AdmissionDecision.refuse(
                f"You already have {current_room_count} open rooms; "
                "close one before creating another"
            )
        if not self._room_limits.allow(username):
            return AdmissionDecision.refuse("Creating rooms too quickly; try again shortly")
        return AdmissionDecision.permit()

    def check_login_backoff(self, username: str) -> AdmissionDecision:
        """Refuse a login that is still inside its post-failure delay."""
        decision = self._login_backoff.check(username)
        if decision.allowed:
            return AdmissionDecision.permit()
        return AdmissionDecision.refuse(
            f"Too many failed attempts; try again in {decision.retry_after_seconds:.0f}s"
        )

    def record_login_failure(self, username: str) -> None:
        self._login_backoff.record_failure(username)

    def record_login_success(self, username: str) -> None:
        self._login_backoff.record_success(username)

    @staticmethod
    def _peer_ip(websocket: Any) -> Optional[str]:
        remote = getattr(websocket, _ATTR_REMOTE_ADDRESS, None)
        if isinstance(remote, (tuple, list)) and remote:
            return str(remote[0])
        return str(remote) if remote else None

    @staticmethod
    def _forwarded_for(websocket: Any) -> Optional[str]:
        headers = getattr(websocket, _ATTR_REQUEST_HEADERS, None)
        if headers is None:
            request = getattr(websocket, _ATTR_REQUEST, None)
            headers = getattr(request, _ATTR_HEADERS, None) if request is not None else None
        if headers is None:
            return None
        try:
            return headers.get(HEADER_FORWARDED_FOR)
        except Exception:
            return None


UsernameRoomCount = Callable[[str], int]
