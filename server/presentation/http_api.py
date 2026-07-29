"""HTTP API — replay, leaderboard, token issuance, and operational endpoints.

Layer: presentation (server/presentation)
Owns: aiohttp routing, request validation at the edge (fail fast on a bad id),
cache directives, and formatting application DTOs into JSON responses.
Must not own: SQL (Database), row->DTO mapping (GameQueryService), token
signing (TokenService), readiness policy (ReadinessProbe), or game rules.
Completed games are public, so those routes need no auth; the handlers stay
thin so the edge can add more.
"""

import logging
import time
from typing import Any, Dict, Optional, Sequence

from aiohttp import web

from server.application.auth_service import AuthService
from server.application.game_query_service import GameQueryService, GameReplay
from server.application.health import ReadinessProbe, ReadinessReport
from server.application.pgn_exporter import to_pgn
from server.application.token_service import TOKEN_TYPE_REFRESH, TokenService
from server.infrastructure.observability.metrics import MetricsRegistry
from server.infrastructure.observability.server_metrics import server_metrics
from server.infrastructure.services.rate_limiter import FixedWindowCounter, resolve_client_ip

_LOGGER = logging.getLogger(__name__)

DEFAULT_HTTP_HOST = "localhost"
DEFAULT_HTTP_PORT = 8080

# Routes. The {game_id} placeholder name must match _parse_game_id's lookup.
_PATH_PARAM_GAME_ID = "game_id"
ROUTE_GAME = "/api/games/{game_id}"
ROUTE_GAME_PGN = "/api/games/{game_id}/pgn"
ROUTE_LEADERBOARD = "/api/leaderboard"

# Operational routes. Deliberately unprefixed: they are the process's own
# health surface, not part of the game API, and Kubernetes probes point here.
ROUTE_HEALTHZ = "/healthz"
ROUTE_READYZ = "/readyz"
ROUTE_METRICS = "/metrics"

# Prometheus' text exposition content type. The version parameter is part of
# the contract; a scraper falls back to guessing without it.
METRICS_CONTENT_TYPE = "text/plain"
METRICS_CHARSET = "utf-8"

_KEY_STATUS = "status"
_KEY_DRAINING = "draining"
_KEY_CHECKS = "checks"
_STATUS_ALIVE = "alive"
_STATUS_READY = "ready"
_STATUS_NOT_READY = "not_ready"

ROUTE_AUTH_LOGIN = "/api/auth/login"
ROUTE_AUTH_REFRESH = "/api/auth/refresh"

PGN_CONTENT_TYPE = "application/x-chess-pgn"
_HEADER_CONTENT_DISPOSITION = "Content-Disposition"
_HEADER_CACHE_CONTROL = "Cache-Control"
_HEADER_ETAG = "ETag"
_HEADER_IF_NONE_MATCH = "If-None-Match"
_HEADER_RETRY_AFTER = "Retry-After"
_HEADER_FORWARDED_FOR = "X-Forwarded-For"
_HEADER_AUTHORIZATION = "Authorization"
_BEARER_PREFIX = "Bearer "

# A row in `games` is written once, inside one transaction, and never updated —
# `room_id` is UNIQUE — so a replay body can never change. That makes it safe to
# cache at a CDN edge effectively forever, which is what keeps history reads off
# the database entirely at the projected rate of finished games.
CACHE_IMMUTABLE = "public, max-age=31536000, immutable"
# The leaderboard changes constantly and matters to nobody within a few seconds,
# so a short shared cache collapses arbitrary read volume into two origin
# queries per minute.
CACHE_LEADERBOARD = "public, max-age=30, stale-while-revalidate=60"
# Token issuance and anything operational must never be stored by a shared
# cache: one cached response would hand a second caller the first one's token.
CACHE_NEVER = "no-store"

# Per-IP HTTP budgets. Generous for reads, tight for the one route that costs a
# bcrypt verification.
DEFAULT_HTTP_REQUESTS_PER_MINUTE = 300
DEFAULT_LOGIN_ATTEMPTS_PER_MINUTE = 10
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMITED_MESSAGE = "Too many requests"
_RATE_LIMIT_RETRY_AFTER_SECONDS = "60"

# Replay JSON body keys.
_KEY_GAME = "game"
_KEY_MOVES = "moves"
_KEY_GAME_ID = "game_id"
_KEY_ROOM_ID = "room_id"
_KEY_WHITE_USERNAME = "white_username"
_KEY_BLACK_USERNAME = "black_username"
_KEY_WINNER_ID = "winner_id"
_KEY_RESULT = "result"
_KEY_WHITE_ELO_BEFORE = "white_elo_before"
_KEY_WHITE_ELO_AFTER = "white_elo_after"
_KEY_BLACK_ELO_BEFORE = "black_elo_before"
_KEY_BLACK_ELO_AFTER = "black_elo_after"
_KEY_STARTED_AT = "started_at"
_KEY_ENDED_AT = "ended_at"
_KEY_MOVE_NUMBER = "move_number"
_KEY_FROM = "from"
_KEY_TO = "to"
_KEY_PIECE = "piece"
_KEY_COLOR = "color"
_KEY_CAPTURED_PIECE = "captured_piece"
_KEY_TIMESTAMP = "timestamp"

# Leaderboard JSON body keys.
_KEY_USERNAME = "username"
_KEY_ELO = "elo"
_KEY_TOTAL_GAMES = "total_games"
_KEY_WINS = "wins"

# Auth JSON body keys.
_KEY_PASSWORD = "password"
_KEY_ACCESS_TOKEN = "access_token"
_KEY_REFRESH_TOKEN = "refresh_token"
_KEY_EXPIRES_IN = "expires_in"
_KEY_TOKEN_TYPE = "token_type"
_KEY_USER_ID = "user_id"
_TOKEN_TYPE_BEARER = "Bearer"

_QUERY_PARAM_LIMIT = "limit"


def _replay_to_dict(replay: GameReplay) -> Dict[str, Any]:
    """Shape a GameReplay DTO into the replay JSON body."""
    return {
        _KEY_GAME: {
            _KEY_GAME_ID: replay.game_id,
            _KEY_ROOM_ID: replay.room_id,
            _KEY_WHITE_USERNAME: replay.white_username,
            _KEY_BLACK_USERNAME: replay.black_username,
            _KEY_WINNER_ID: replay.winner_id,
            _KEY_RESULT: replay.result,
            _KEY_WHITE_ELO_BEFORE: replay.white_elo_before,
            _KEY_WHITE_ELO_AFTER: replay.white_elo_after,
            _KEY_BLACK_ELO_BEFORE: replay.black_elo_before,
            _KEY_BLACK_ELO_AFTER: replay.black_elo_after,
            _KEY_STARTED_AT: replay.started_at,
            _KEY_ENDED_AT: replay.ended_at,
        },
        _KEY_MOVES: [
            {
                _KEY_MOVE_NUMBER: move.move_number,
                _KEY_FROM: move.from_square,
                _KEY_TO: move.to_square,
                _KEY_PIECE: move.piece_type,
                _KEY_COLOR: move.piece_color,
                _KEY_CAPTURED_PIECE: move.captured_piece,
                _KEY_TIMESTAMP: move.timestamp,
            }
            for move in replay.moves
        ],
    }


def _parse_game_id(request: web.Request) -> int:
    """Validate the {game_id} path segment, failing fast with 400 if non-integer."""
    raw = request.match_info[_PATH_PARAM_GAME_ID]
    try:
        return int(raw)
    except ValueError:
        raise web.HTTPBadRequest(reason=f"Invalid game id: {raw!r}")


def _parse_limit(request: web.Request) -> Optional[int]:
    """Read an optional `?limit=` query parameter, failing fast if malformed.

    The value is only bounded downstream — GameQueryService clamps it — so this
    rejects non-numeric input and leaves the ceiling to the one place that owns
    it, rather than restating the maximum at every edge.
    """
    raw = request.query.get(_QUERY_PARAM_LIMIT)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise web.HTTPBadRequest(reason=f"Invalid limit: {raw!r}")


def _game_etag(game_id: int) -> str:
    """A validator for an immutable resource.

    The id alone is a complete ETag here precisely because the body can never
    change for a given id — there is no version or timestamp to fold in.
    """
    return f'"game-{game_id}"'


class HttpApi:
    """HTTP endpoints: replay/leaderboard reads, token issuance, and probes.

    Handlers are methods so they close over their collaborators without a module
    global; routing lives in `build_app`, mirroring how ws_server keeps its
    dispatch table in one place.

    Every collaborator except the query service is optional, because the routes
    they enable are optional: a deployment with no signing key configured simply
    does not expose token issuance, rather than exposing it unsigned.
    """

    def __init__(
        self,
        query_service: Optional[GameQueryService] = None,
        readiness: Optional[ReadinessProbe] = None,
        metrics_registry: Optional[MetricsRegistry] = None,
        auth_service: Optional[AuthService] = None,
        token_service: Optional[TokenService] = None,
        trusted_proxies: Sequence[str] = (),
        requests_per_minute: int = DEFAULT_HTTP_REQUESTS_PER_MINUTE,
        login_attempts_per_minute: int = DEFAULT_LOGIN_ATTEMPTS_PER_MINUTE,
    ) -> None:
        self._query_service = query_service
        # Building the catalogue against the default registry is what makes a
        # scrape of an API-only process non-empty: nothing else in that process
        # constructs it, and an endpoint that renders nothing looks identical to
        # a healthy service with no traffic.
        self._metrics = server_metrics(metrics_registry)
        self._metrics_registry = metrics_registry or self._metrics.registry
        self._readiness = readiness
        self._auth_service = auth_service
        self._token_service = token_service
        self._trusted_proxies = frozenset(trusted_proxies)
        self._request_limits = FixedWindowCounter(requests_per_minute, _RATE_LIMIT_WINDOW_SECONDS)
        self._login_limits = FixedWindowCounter(
            login_attempts_per_minute, _RATE_LIMIT_WINDOW_SECONDS
        )

    @property
    def issues_tokens(self) -> bool:
        return self._token_service is not None and self._auth_service is not None

    @property
    def serves_history(self) -> bool:
        return self._query_service is not None

    def build_app(self) -> web.Application:
        """Register the routes this deployment's collaborators can actually serve.

        The operational routes are unconditional: every role — API, gateway,
        game authority, persistence worker — is probed and scraped the same way,
        which is what lets one set of manifests describe all of them.

        The history and token routes are not. A persistence worker answers no
        client, and registering `/api/games/{id}` on one would advertise an
        endpoint whose only possible response is a 500 — worse than a 404,
        because a load balancer would route real traffic to it.
        """
        app = web.Application(middlewares=[self._rate_limit_middleware])
        app.router.add_get(ROUTE_HEALTHZ, self.get_healthz)
        app.router.add_get(ROUTE_READYZ, self.get_readyz)
        app.router.add_get(ROUTE_METRICS, self.get_metrics)
        if self.serves_history:
            app.router.add_get(ROUTE_GAME, self.get_game)
            app.router.add_get(ROUTE_GAME_PGN, self.get_game_pgn)
            app.router.add_get(ROUTE_LEADERBOARD, self.get_leaderboard)
        if self.issues_tokens:
            app.router.add_post(ROUTE_AUTH_LOGIN, self.post_login)
            app.router.add_post(ROUTE_AUTH_REFRESH, self.post_refresh)
        return app

    # ------------------------------------------------------------------
    # Game history — immutable, and cached as such
    # ------------------------------------------------------------------

    async def get_game(self, request: web.Request) -> web.Response:
        game_id = _parse_game_id(request)
        not_modified = self._not_modified_response(request, game_id)
        if not_modified is not None:
            return not_modified

        replay = await self._query_service.get_replay(game_id)
        if replay is None:
            raise web.HTTPNotFound(reason=f"No game with id {game_id}")
        return web.json_response(
            _replay_to_dict(replay), headers=self._immutable_headers(game_id)
        )

    async def get_game_pgn(self, request: web.Request) -> web.Response:
        game_id = _parse_game_id(request)
        not_modified = self._not_modified_response(request, game_id)
        if not_modified is not None:
            return not_modified

        replay = await self._query_service.get_replay(game_id)
        if replay is None:
            raise web.HTTPNotFound(reason=f"No game with id {game_id}")

        headers = self._immutable_headers(game_id)
        headers[_HEADER_CONTENT_DISPOSITION] = f'attachment; filename="{replay.room_id}.pgn"'
        return web.Response(text=to_pgn(replay), content_type=PGN_CONTENT_TYPE, headers=headers)

    async def get_leaderboard(self, request: web.Request) -> web.Response:
        limit = _parse_limit(request)
        rows = (
            await self._query_service.get_leaderboard(limit)
            if limit is not None
            else await self._query_service.get_leaderboard()
        )
        body = [
            {
                _KEY_USERNAME: row.username,
                _KEY_ELO: row.elo,
                _KEY_TOTAL_GAMES: row.total_games,
                _KEY_WINS: row.wins,
            }
            for row in rows
        ]
        return web.json_response(body, headers={_HEADER_CACHE_CONTROL: CACHE_LEADERBOARD})

    def _not_modified_response(self, request: web.Request, game_id: int) -> Optional[web.Response]:
        """Answer 304 when the caller already holds this immutable resource.

        Checked before the database read, not after: the entire point of an
        immutable resource is that a revalidation costs no query.
        """
        if request.headers.get(_HEADER_IF_NONE_MATCH) != _game_etag(game_id):
            return None
        return web.Response(status=304, headers=self._immutable_headers(game_id))

    @staticmethod
    def _immutable_headers(game_id: int) -> Dict[str, str]:
        return {_HEADER_CACHE_CONTROL: CACHE_IMMUTABLE, _HEADER_ETAG: _game_etag(game_id)}

    # ------------------------------------------------------------------
    # Token issuance — the API tier's half of the auth split
    # ------------------------------------------------------------------

    async def post_login(self, request: web.Request) -> web.Response:
        """Verify credentials once and hand back an access/refresh pair.

        This is the only route in the system that pays for a bcrypt
        verification. Every WebSocket connection afterwards presents the access
        token instead, which the socket tier checks with a signature comparison
        and no user-table read at all.
        """
        body = await self._read_json_object(request)
        username = body.get(_KEY_USERNAME)
        password = body.get(_KEY_PASSWORD)
        if not isinstance(username, str) or not isinstance(password, str):
            raise web.HTTPBadRequest(reason="Login requires 'username' and 'password'")

        result = await self._auth_service.login(username, password)
        if not result.is_ok:
            raise web.HTTPUnauthorized(
                reason=result.error, headers={_HEADER_CACHE_CONTROL: CACHE_NEVER}
            )

        user_id, resolved_username, elo = result.value
        pair = self._token_service.issue_pair(user_id, resolved_username, elo)
        return self._token_response(pair, user_id, resolved_username, elo)

    async def post_refresh(self, request: web.Request) -> web.Response:
        """Exchange a valid refresh token for a fresh access token.

        The identity comes from the token's own claims, never from the request
        body: a refresh that let the caller name the account it refreshes into
        would be an impersonation route wearing a rotation route's name.
        """
        body = await self._read_json_object(request)
        verified = self._token_service.verify(
            body.get(_KEY_REFRESH_TOKEN), expected_type=TOKEN_TYPE_REFRESH
        )
        if not verified.is_ok:
            raise web.HTTPUnauthorized(
                reason=verified.error, headers={_HEADER_CACHE_CONTROL: CACHE_NEVER}
            )

        claims = verified.value
        pair = self._token_service.issue_pair(claims.user_id, claims.username, claims.elo)
        return self._token_response(pair, claims.user_id, claims.username, claims.elo)

    @staticmethod
    def _token_response(pair: Any, user_id: int, username: str, elo: int) -> web.Response:
        return web.json_response(
            {
                _KEY_ACCESS_TOKEN: pair.access_token,
                _KEY_REFRESH_TOKEN: pair.refresh_token,
                _KEY_TOKEN_TYPE: _TOKEN_TYPE_BEARER,
                _KEY_EXPIRES_IN: pair.expires_in_seconds,
                _KEY_USER_ID: user_id,
                _KEY_USERNAME: username,
                _KEY_ELO: elo,
            },
            headers={_HEADER_CACHE_CONTROL: CACHE_NEVER},
        )

    @staticmethod
    async def _read_json_object(request: web.Request) -> Dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(reason="Request body must be JSON")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(reason="Request body must be a JSON object")
        return body

    # ------------------------------------------------------------------
    # Operational surface
    # ------------------------------------------------------------------

    async def get_healthz(self, request: web.Request) -> web.Response:
        """Liveness: the process is up and its event loop is turning.

        Touches nothing external on purpose — see ReadinessProbe's docstring for
        why a liveness check that reads the database is a restart storm waiting
        for a slow query.
        """
        return web.json_response(
            {_KEY_STATUS: _STATUS_ALIVE}, headers={_HEADER_CACHE_CONTROL: CACHE_NEVER}
        )

    async def get_readyz(self, request: web.Request) -> web.Response:
        """Readiness: this replica can serve work right now.

        503 is the correct refusal: it tells a load balancer to route elsewhere
        while leaving the process alive and its in-flight games running.
        """
        report = (
            await self._readiness.evaluate()
            if self._readiness is not None
            else ReadinessReport(ready=True, draining=False, checks={})
        )
        return web.json_response(
            {
                _KEY_STATUS: _STATUS_READY if report.ready else _STATUS_NOT_READY,
                _KEY_DRAINING: report.draining,
                _KEY_CHECKS: dict(report.checks),
            },
            status=200 if report.ready else 503,
            headers={_HEADER_CACHE_CONTROL: CACHE_NEVER},
        )

    async def get_metrics(self, request: web.Request) -> web.Response:
        return web.Response(
            text=self._metrics_registry.render(),
            content_type=METRICS_CONTENT_TYPE,
            charset=METRICS_CHARSET,
            headers={_HEADER_CACHE_CONTROL: CACHE_NEVER},
        )

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    @web.middleware
    async def _rate_limit_middleware(self, request: web.Request, handler) -> web.Response:
        """Bound how often one caller may reach the API, and measure what it did.

        Probes and metrics are exempt from both: they are called by the
        orchestrator on a fixed schedule, so rate-limiting a liveness probe gets
        the replica killed by its own protection, and counting them would bury
        real traffic under a constant background rate.
        """
        if request.path in (ROUTE_HEALTHZ, ROUTE_READYZ, ROUTE_METRICS):
            return await handler(request)

        self._enforce_rate_limit(request)
        started_at = time.monotonic()
        try:
            response = await handler(request)
        except web.HTTPException as refusal:
            self._record_request(started_at, refusal.status)
            raise
        self._record_request(started_at, response.status)
        return response

    def _enforce_rate_limit(self, request: web.Request) -> None:
        key = self._rate_limit_key(request)
        limiter = self._login_limits if request.path == ROUTE_AUTH_LOGIN else self._request_limits
        if limiter.allow(key):
            return

        self._metrics.http_rate_limited.increment()
        _LOGGER.warning("Rate limited %s %s for %s", request.method, request.path, key)
        raise web.HTTPTooManyRequests(
            reason=_RATE_LIMITED_MESSAGE,
            headers={
                _HEADER_RETRY_AFTER: _RATE_LIMIT_RETRY_AFTER_SECONDS,
                _HEADER_CACHE_CONTROL: CACHE_NEVER,
            },
        )

    def _record_request(self, started_at: float, status: int) -> None:
        self._metrics.http_requests.increment()
        self._metrics.http_request_duration_seconds.observe(time.monotonic() - started_at)
        if status >= 400:
            self._metrics.http_errors.increment()

    def _rate_limit_key(self, request: web.Request) -> str:
        """Key a request by its bearer token if it has one, else by client IP.

        A token is the better key when present: it survives a client changing
        networks and it cannot be shared by everyone behind one NAT.
        """
        bearer = request.headers.get(_HEADER_AUTHORIZATION, "")
        if bearer.startswith(_BEARER_PREFIX) and self._token_service is not None:
            verified = self._token_service.verify(bearer[len(_BEARER_PREFIX):])
            if verified.is_ok:
                return f"user:{verified.value.user_id}"
        return f"ip:{self._client_ip(request)}"

    def _client_ip(self, request: web.Request) -> str:
        peer_ip = request.remote
        return (
            resolve_client_ip(
                peer_ip,
                request.headers.get(_HEADER_FORWARDED_FOR),
                peer_is_trusted_proxy=peer_ip in self._trusted_proxies,
            )
            or "unknown"
        )


class HttpApiServer:
    """Owns the aiohttp app's runtime lifecycle.

    Accepts either a ready-made `HttpApi` or the query service to build a bare
    one from, so a caller that wires probes, tokens and metrics does it once in
    the composition root rather than restating every collaborator here.
    """

    def __init__(
        self,
        query_service: Optional[GameQueryService] = None,
        host: str = DEFAULT_HTTP_HOST,
        port: int = DEFAULT_HTTP_PORT,
        api: Optional[HttpApi] = None,
        ssl_context: Optional[Any] = None,
    ) -> None:
        if api is None and query_service is None:
            raise ValueError("HttpApiServer needs either an HttpApi or a GameQueryService")
        self._api = api or HttpApi(query_service)
        self._host = host
        self._port = port
        self._ssl_context = ssl_context
        self._runner: Optional[web.AppRunner] = None

    @property
    def api(self) -> HttpApi:
        return self._api

    async def start(self) -> None:
        self._runner = web.AppRunner(self._api.build_app())
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port, ssl_context=self._ssl_context)
        await site.start()
        scheme = "https" if self._ssl_context is not None else "http"
        _LOGGER.info("HTTP API running on %s://%s:%d", scheme, self._host, self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
        _LOGGER.info("HTTP API stopped")
