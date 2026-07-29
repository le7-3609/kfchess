"""Rate limiting primitives — token buckets, fixed windows, and failure backoff.

Owns: the counting itself, and the eviction that keeps a per-key limiter from
becoming an unbounded map keyed by attacker-chosen values.
Must not own: which paths are limited or with what budget — callers own the
policy, and the presentation layer decides what a refusal looks like on the
wire.

Everything here is in-process, which limits per replica rather than per user.
That is the correct shape for the single-process server that exists today, and
Server_Design.md section 6.4 moves the same counters to Redis in Step 5 so the
budget follows the user across replicas. The interfaces are the ones that
survive that move: a key goes in, an allow/deny comes out.
"""

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

# A per-key limiter is a map an unauthenticated caller can grow by varying the
# key (a spoofed source address, a fresh username per attempt). Capping the
# number of tracked keys turns that from a memory-exhaustion vector into, at
# worst, an evicted counter.
DEFAULT_MAX_TRACKED_KEYS = 100_000


class TokenBucket:
    """Allows a steady *rate* per second with a *burst* allowance on top.

    The right shape for a per-socket frame limit: real play is bursty (a flurry
    of clicks) around a low average, so a fixed window either rejects legitimate
    bursts or has to be set so high it stops limiting anything.
    """

    def __init__(self, rate_per_second: float, burst: float, time_fn: Callable[[], float] = time.monotonic) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        self._rate = rate_per_second
        self._burst = burst
        self._time_fn = time_fn
        self._tokens = float(burst)
        self._updated_at = time_fn()

    @property
    def available(self) -> float:
        return self._tokens

    def allow(self, cost: float = 1.0) -> bool:
        """Consume *cost* tokens if the bucket has them, reporting success."""
        self._refill()
        if self._tokens < cost:
            return False
        self._tokens -= cost
        return True

    def _refill(self) -> None:
        now = self._time_fn()
        elapsed = max(0.0, now - self._updated_at)
        self._updated_at = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)


class KeyedTokenBuckets:
    """One TokenBucket per key, with bounded key retention."""

    def __init__(
        self,
        rate_per_second: float,
        burst: float,
        max_keys: int = DEFAULT_MAX_TRACKED_KEYS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._rate = rate_per_second
        self._burst = burst
        self._max_keys = max_keys
        self._time_fn = time_fn
        self._buckets: "OrderedDict[str, TokenBucket]" = OrderedDict()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(self._rate, self._burst, self._time_fn)
            self._buckets[key] = bucket
            self._evict_if_needed()
        self._buckets.move_to_end(key)
        return bucket.allow(cost)

    def forget(self, key: str) -> None:
        self._buckets.pop(key, None)

    def _evict_if_needed(self) -> None:
        while len(self._buckets) > self._max_keys:
            evicted, _ = self._buckets.popitem(last=False)
            _LOGGER.debug("Evicted rate-limit bucket for key %r under key pressure", evicted)


class FixedWindowCounter:
    """Counts events per key inside a rolling window of *window_seconds*.

    Used where the budget is naturally "N per minute" — new connections from one
    address, rooms created by one user — and where a burst is not something to
    accommodate.
    """

    def __init__(
        self,
        limit: int,
        window_seconds: float,
        max_keys: int = DEFAULT_MAX_TRACKED_KEYS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._limit = limit
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._time_fn = time_fn
        self._windows: "OrderedDict[str, Tuple[float, int]]" = OrderedDict()

    @property
    def limit(self) -> int:
        return self._limit

    def allow(self, key: str) -> bool:
        """Count one event against *key*, reporting whether it fits the budget."""
        now = self._time_fn()
        window_start, count = self._windows.get(key, (now, 0))
        if now - window_start >= self._window_seconds:
            window_start, count = now, 0

        if count >= self._limit:
            self._windows[key] = (window_start, count)
            return False

        self._windows[key] = (window_start, count + 1)
        self._windows.move_to_end(key)
        self._evict_if_needed()
        return True

    def forget(self, key: str) -> None:
        self._windows.pop(key, None)

    def _evict_if_needed(self) -> None:
        while len(self._windows) > self._max_keys:
            self._windows.popitem(last=False)


@dataclass(frozen=True)
class BackoffDecision:
    """Whether an attempt may proceed, and how long until one may."""

    allowed: bool
    retry_after_seconds: float


# Failures allowed at full speed before any delay is imposed. Two, so that a
# connection's third and final attempt (MAX_AUTH_ATTEMPTS) is still undelayed
# and a person mistyping a password never notices this exists — while a fourth
# attempt, which no longer looks like typing, pays.
DEFAULT_FREE_ATTEMPTS = 2


class ExponentialBackoffLimiter:
    """Delays repeated failures for a key, doubling the wait each time.

    Applied per username on the auth path. A per-IP connection limit alone does
    not stop a distributed password guess against one account — every attempt
    can come from a different address — while this is keyed by the account being
    attacked, so it holds however the attempts arrive.

    The first *free_attempts* failures cost nothing, which is what keeps the
    protection invisible to a legitimate user and leaves the connection-level
    retry budget meaningful.
    """

    def __init__(
        self,
        initial_delay_seconds: float = 1.0,
        multiplier: float = 2.0,
        max_delay_seconds: float = 60.0,
        free_attempts: int = DEFAULT_FREE_ATTEMPTS,
        max_keys: int = DEFAULT_MAX_TRACKED_KEYS,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._initial_delay_seconds = initial_delay_seconds
        self._multiplier = multiplier
        self._max_delay_seconds = max_delay_seconds
        self._free_attempts = max(0, free_attempts)
        self._max_keys = max_keys
        self._time_fn = time_fn
        # key -> (consecutive_failures, earliest_next_attempt)
        self._state: "OrderedDict[str, Tuple[int, float]]" = OrderedDict()

    def check(self, key: str) -> BackoffDecision:
        """Report whether *key* may attempt now, without recording an attempt."""
        failures, next_allowed_at = self._state.get(key, (0, 0.0))
        if failures <= self._free_attempts:
            return BackoffDecision(allowed=True, retry_after_seconds=0.0)
        remaining = next_allowed_at - self._time_fn()
        if remaining <= 0:
            return BackoffDecision(allowed=True, retry_after_seconds=0.0)
        return BackoffDecision(allowed=False, retry_after_seconds=remaining)

    def record_failure(self, key: str) -> float:
        """Register a failed attempt and return the delay now imposed."""
        failures, _ = self._state.get(key, (0, 0.0))
        failures += 1
        delay = (
            0.0
            if failures <= self._free_attempts
            else min(
                self._initial_delay_seconds
                * (self._multiplier ** (failures - self._free_attempts - 1)),
                self._max_delay_seconds,
            )
        )
        self._state[key] = (failures, self._time_fn() + delay)
        self._state.move_to_end(key)
        self._evict_if_needed()
        return delay

    def record_success(self, key: str) -> None:
        """Clear a key's history — a correct credential ends the backoff."""
        self._state.pop(key, None)

    def _evict_if_needed(self) -> None:
        while len(self._state) > self._max_keys:
            self._state.popitem(last=False)


class ConcurrencyLimiter:
    """Caps how many simultaneous things one key may hold, e.g. rooms per user."""

    def __init__(self, limit: int) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        self._limit = limit
        self._held: Dict[str, int] = {}

    @property
    def limit(self) -> int:
        return self._limit

    def acquire(self, key: str) -> bool:
        current = self._held.get(key, 0)
        if current >= self._limit:
            return False
        self._held[key] = current + 1
        return True

    def release(self, key: str) -> None:
        current = self._held.get(key, 0)
        if current <= 1:
            self._held.pop(key, None)
            return
        self._held[key] = current - 1

    def held_by(self, key: str) -> int:
        return self._held.get(key, 0)


def resolve_client_ip(
    peer_ip: Optional[str],
    forwarded_for: Optional[str],
    peer_is_trusted_proxy: bool,
) -> Optional[str]:
    """Resolve the address a per-IP limit should be keyed by.

    `X-Forwarded-For` is only consulted when the connection itself arrived from
    a trusted ingress. Trusting it unconditionally would make every per-IP limit
    in the server bypassable by setting a header, which is worse than having no
    limit at all — it looks like protection and is not.

    The *first* entry is taken because a proxy appends, so the leftmost value is
    the original client as the nearest trusted hop saw it.
    """
    if peer_is_trusted_proxy and forwarded_for:
        first = forwarded_for.split(",")[0].strip()
        if first:
            return first
    return peer_ip
