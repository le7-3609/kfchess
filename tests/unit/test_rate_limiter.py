"""Unit tests for the rate-limiting primitives and proxy-header resolution.

Every limiter here is driven by an injected clock rather than by sleeping, so
the refill and window-rollover behaviour is asserted exactly instead of raced.
"""

import pytest

from server.infrastructure.services.rate_limiter import (
    ConcurrencyLimiter,
    ExponentialBackoffLimiter,
    FixedWindowCounter,
    KeyedTokenBuckets,
    TokenBucket,
    resolve_client_ip,
)


class _Clock:
    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_token_bucket_allows_a_burst_then_refuses():
    clock = _Clock()
    bucket = TokenBucket(rate_per_second=1.0, burst=3, time_fn=clock)

    assert [bucket.allow() for _ in range(3)] == [True, True, True]
    assert not bucket.allow()


def test_token_bucket_refills_at_its_rate():
    """Real play is bursty around a low average, which is exactly what a bucket
    accommodates and a fixed window does not."""
    clock = _Clock()
    bucket = TokenBucket(rate_per_second=2.0, burst=2, time_fn=clock)
    bucket.allow()
    bucket.allow()

    clock.now += 0.5  # one token's worth
    assert bucket.allow()
    assert not bucket.allow()


def test_token_bucket_never_refills_past_its_burst():
    clock = _Clock()
    bucket = TokenBucket(rate_per_second=100.0, burst=2, time_fn=clock)

    clock.now += 3600
    assert bucket.allow()
    assert bucket.allow()
    assert not bucket.allow()


@pytest.mark.parametrize("rate,burst", [(0, 1), (-1, 1), (1, 0), (1, -1)])
def test_token_bucket_rejects_a_meaningless_budget(rate, burst):
    with pytest.raises(ValueError):
        TokenBucket(rate_per_second=rate, burst=burst)


def test_keyed_buckets_are_independent_per_key():
    clock = _Clock()
    buckets = KeyedTokenBuckets(rate_per_second=1.0, burst=1, time_fn=clock)

    assert buckets.allow("alice")
    assert not buckets.allow("alice")
    assert buckets.allow("bob")


def test_keyed_buckets_evict_the_least_recently_used_key_under_pressure():
    """The key is attacker-chosen, so an uncapped map is a memory-exhaustion
    vector; eviction turns that into, at worst, a forgotten counter."""
    buckets = KeyedTokenBuckets(rate_per_second=1.0, burst=1, max_keys=2, time_fn=_Clock())

    for key in ("a", "b", "c"):
        buckets.allow(key)

    # "a" was evicted, so it gets a fresh bucket; "c" is still tracked.
    assert buckets.allow("a")
    assert not buckets.allow("c")


def test_fixed_window_counts_then_refuses_until_the_window_rolls():
    clock = _Clock()
    counter = FixedWindowCounter(limit=2, window_seconds=60.0, time_fn=clock)

    assert counter.allow("ip") and counter.allow("ip")
    assert not counter.allow("ip")

    clock.now += 60
    assert counter.allow("ip")


def test_an_over_limit_key_does_not_extend_its_own_window():
    """A refused attempt must not push the window forward, or a client hammering
    the limit would never be let back in."""
    clock = _Clock()
    counter = FixedWindowCounter(limit=1, window_seconds=10.0, time_fn=clock)
    counter.allow("ip")

    clock.now += 5
    assert not counter.allow("ip")

    clock.now += 5
    assert counter.allow("ip")


def test_backoff_leaves_the_free_failures_undelayed():
    """A person mistyping a password must not be slowed down, and the
    connection-level retry budget has to stay meaningful."""
    clock = _Clock()
    backoff = ExponentialBackoffLimiter(free_attempts=3, time_fn=clock)

    for _ in range(3):
        assert backoff.record_failure("alice") == 0.0
        assert backoff.check("alice").allowed


def test_backoff_delays_and_doubles_after_the_free_attempts():
    clock = _Clock()
    backoff = ExponentialBackoffLimiter(
        initial_delay_seconds=1.0, multiplier=2.0, free_attempts=1, time_fn=clock
    )
    assert backoff.record_failure("alice") == 0.0

    assert backoff.record_failure("alice") == 1.0
    assert not backoff.check("alice").allowed

    clock.now += 1.0
    assert backoff.check("alice").allowed
    assert backoff.record_failure("alice") == 2.0


def test_backoff_is_capped():
    clock = _Clock()
    backoff = ExponentialBackoffLimiter(
        initial_delay_seconds=1.0, multiplier=10.0, max_delay_seconds=5.0,
        free_attempts=0, time_fn=clock,
    )

    delays = [backoff.record_failure("alice") for _ in range(5)]

    assert max(delays) == 5.0


def test_a_correct_credential_clears_the_backoff():
    clock = _Clock()
    backoff = ExponentialBackoffLimiter(free_attempts=0, time_fn=clock)
    backoff.record_failure("alice")
    assert not backoff.check("alice").allowed

    backoff.record_success("alice")

    assert backoff.check("alice").allowed


def test_backoff_is_keyed_by_account_not_by_source():
    """A distributed guess arrives from a different address every time, so a
    per-IP limit alone would never see it."""
    clock = _Clock()
    backoff = ExponentialBackoffLimiter(free_attempts=0, time_fn=clock)

    backoff.record_failure("victim")

    assert not backoff.check("victim").allowed
    assert backoff.check("someone-else").allowed


def test_concurrency_limiter_caps_and_releases():
    limiter = ConcurrencyLimiter(limit=2)

    assert limiter.acquire("alice") and limiter.acquire("alice")
    assert not limiter.acquire("alice")

    limiter.release("alice")
    assert limiter.acquire("alice")
    assert limiter.held_by("alice") == 2


def test_forwarded_for_is_honoured_only_from_a_trusted_peer():
    """Trusting the header from any peer makes every per-IP limit in the server
    bypassable with one header — worse than no limit, because it looks like
    protection."""
    assert (
        resolve_client_ip("10.0.0.1", "203.0.113.9", peer_is_trusted_proxy=True) == "203.0.113.9"
    )
    assert (
        resolve_client_ip("10.0.0.1", "203.0.113.9", peer_is_trusted_proxy=False) == "10.0.0.1"
    )


def test_the_leftmost_forwarded_entry_is_the_client():
    """A proxy appends, so the original client is the leftmost value as the
    nearest trusted hop saw it."""
    resolved = resolve_client_ip(
        "10.0.0.1", "203.0.113.9, 70.41.3.18, 10.0.0.1", peer_is_trusted_proxy=True
    )

    assert resolved == "203.0.113.9"


def test_an_empty_forwarded_header_falls_back_to_the_peer():
    assert resolve_client_ip("10.0.0.1", "   ", peer_is_trusted_proxy=True) == "10.0.0.1"
    assert resolve_client_ip("10.0.0.1", None, peer_is_trusted_proxy=True) == "10.0.0.1"
