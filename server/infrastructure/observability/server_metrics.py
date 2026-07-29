"""The server's metric catalogue — one place naming everything it exports.

Owns: the metric names, their help text, and the accessors the rest of the
server updates them through.
Must not own: metric storage or rendering (metrics.py), nor the decision to
scrape (presentation serves the endpoint).

Counters and the histogram are created eagerly, so a scrape before the first
event still shows a zero rather than an absent series — a missing series and a
series pinned at zero look identical on a graph but mean opposite things.

Gauges that read a live object are bound at composition time instead, because
the object they read does not exist until the server is built.
"""

from typing import Callable, Optional

from server.infrastructure.observability.metrics import (
    Counter,
    Histogram,
    MetricsRegistry,
    default_registry,
)

METRIC_ROOMS_ACTIVE = "kfchess_rooms_active"
METRIC_SESSIONS_ACTIVE = "kfchess_ws_sessions_active"
METRIC_QUEUE_LENGTH = "kfchess_matchmaking_queue_length"
METRIC_COUNTDOWNS_ACTIVE = "kfchess_disconnect_countdowns_active"
METRIC_TICK_DURATION = "kfchess_tick_duration_seconds"
METRIC_BROADCAST_FAILURES = "kfchess_broadcast_failures_total"
METRIC_FRAMES_RECEIVED = "kfchess_frames_received_total"
METRIC_FRAMES_RATE_LIMITED = "kfchess_frames_rate_limited_total"
METRIC_CONNECTIONS_REJECTED = "kfchess_connections_rejected_total"
METRIC_AUTH_FAILURES = "kfchess_auth_failures_total"
METRIC_MOVES_DEDUPLICATED = "kfchess_moves_deduplicated_total"
METRIC_GAMES_PERSISTED = "kfchess_games_persisted_total"
METRIC_GAMES_ALREADY_PERSISTED = "kfchess_games_already_persisted_total"
METRIC_HTTP_REQUESTS = "kfchess_http_requests_total"
METRIC_HTTP_ERRORS = "kfchess_http_errors_total"
METRIC_HTTP_RATE_LIMITED = "kfchess_http_rate_limited_total"
METRIC_HTTP_REQUEST_DURATION = "kfchess_http_request_duration_seconds"

# Request latencies span a much wider range than a simulation tick — a cached
# leaderboard read and a bcrypt-backed login are three orders of magnitude
# apart — so the HTTP histogram gets its own, wider buckets.
HTTP_BUCKETS_SECONDS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class ServerMetrics:
    """Typed handles for every metric this server exports."""

    def __init__(self, registry: MetricsRegistry) -> None:
        self._registry = registry
        self.tick_duration_seconds: Histogram = registry.histogram(
            METRIC_TICK_DURATION,
            "Wall-clock duration of one simulation tick, including its broadcast. "
            "A 20 Hz runner has a 50 ms budget; time past it is simulation lag "
            "that every player in every room on this process feels.",
        )
        self.broadcast_failures: Counter = registry.counter(
            METRIC_BROADCAST_FAILURES,
            "Outbound room broadcasts that raised while being sent to a recipient.",
        )
        self.frames_received: Counter = registry.counter(
            METRIC_FRAMES_RECEIVED, "Client frames accepted for dispatch."
        )
        self.frames_rate_limited: Counter = registry.counter(
            METRIC_FRAMES_RATE_LIMITED, "Client frames dropped by the per-session rate limit."
        )
        self.connections_rejected: Counter = registry.counter(
            METRIC_CONNECTIONS_REJECTED, "Connections refused by the per-IP connection rate limit."
        )
        self.auth_failures: Counter = registry.counter(
            METRIC_AUTH_FAILURES, "Failed authentication attempts."
        )
        self.moves_deduplicated: Counter = registry.counter(
            METRIC_MOVES_DEDUPLICATED,
            "Repeated move frames answered from the idempotency cache instead of re-executed.",
        )
        self.games_persisted: Counter = registry.counter(
            METRIC_GAMES_PERSISTED, "Finished games written to the database."
        )
        self.games_already_persisted: Counter = registry.counter(
            METRIC_GAMES_ALREADY_PERSISTED,
            "Finished-game saves that found the room already persisted — the "
            "idempotent path, not a failure.",
        )
        # The API tier's RED metrics. A slow login is invisible in game metrics,
        # and after the roles are split there is no shared process for it to
        # show up in at all.
        self.http_requests: Counter = registry.counter(
            METRIC_HTTP_REQUESTS, "HTTP requests handled."
        )
        self.http_errors: Counter = registry.counter(
            METRIC_HTTP_ERRORS, "HTTP requests answered with a 4xx or 5xx status."
        )
        self.http_rate_limited: Counter = registry.counter(
            METRIC_HTTP_RATE_LIMITED, "HTTP requests refused by the per-caller rate limit."
        )
        self.http_request_duration_seconds: Histogram = registry.histogram(
            METRIC_HTTP_REQUEST_DURATION,
            "Wall-clock duration of one HTTP request.",
            buckets=HTTP_BUCKETS_SECONDS,
        )

    @property
    def registry(self) -> MetricsRegistry:
        return self._registry

    def bind_rooms_active(self, source: Callable[[], float]) -> None:
        self._registry.gauge(METRIC_ROOMS_ACTIVE, "Game rooms currently registered.", source)

    def bind_sessions_active(self, source: Callable[[], float]) -> None:
        self._registry.gauge(
            METRIC_SESSIONS_ACTIVE, "WebSocket sessions currently connected.", source
        )

    def bind_queue_length(self, source: Callable[[], float]) -> None:
        self._registry.gauge(
            METRIC_QUEUE_LENGTH, "Players waiting in the matchmaking queue.", source
        )

    def bind_countdowns_active(self, source: Callable[[], float]) -> None:
        self._registry.gauge(
            METRIC_COUNTDOWNS_ACTIVE,
            "Disconnect countdowns running across all rooms — players who dropped "
            "and have not yet forfeited or returned.",
            source,
        )

    def unbind_live_gauges(self) -> None:
        """Drop the gauges that read live objects, on server teardown.

        Without this a stopped server's registry keeps calling into a
        room manager and session table that are no longer being maintained,
        which in a test process means the next server's scrape reports the
        previous one's objects.
        """
        for name in (
            METRIC_ROOMS_ACTIVE,
            METRIC_SESSIONS_ACTIVE,
            METRIC_QUEUE_LENGTH,
            METRIC_COUNTDOWNS_ACTIVE,
        ):
            self._registry.unregister(name)


_DEFAULT_METRICS: Optional[ServerMetrics] = None


def server_metrics(registry: Optional[MetricsRegistry] = None) -> ServerMetrics:
    """The process-wide metric handles, created on first use.

    Accepting an explicit *registry* keeps tests able to measure against a
    throwaway registry without touching the default one.
    """
    global _DEFAULT_METRICS
    if registry is not None:
        return ServerMetrics(registry)
    if _DEFAULT_METRICS is None:
        _DEFAULT_METRICS = ServerMetrics(default_registry())
    return _DEFAULT_METRICS
