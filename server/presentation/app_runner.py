"""Composition root — parses configuration and builds the process's object graph.

Layer: presentation (server/presentation)
Owns: CLI/environment parsing, TLS context construction, and the wiring of one
of the three server roles (API, WebSocket, or both) together with its
dependencies, health probes and graceful-drain handling.
Must not own: any behaviour. Every decision here is which object to construct
and hand to which collaborator; nothing in this module implements a rule, a
query, or a frame.

The three roles exist because the API tier and the WebSocket tier have almost
nothing in common operationally — connections lasting milliseconds against
connections lasting hours, request rate against open-connection count, and a
restart that costs nothing against a restart that disconnects every player
mid-game. Running them on one event loop means a burst of replay queries
competes with the loop that forwards moves, and deploying a leaderboard change
drops every live socket.

`run_combined` remains for local development, where one process is simply more
convenient than two.
"""

import argparse
import asyncio
import logging
import os
import signal
import ssl
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from server.application.auth_service import AuthService
from server.application.game_query_service import GameQueryService
from server.application.health import ReadinessProbe
from server.application.token_service import TokenService
from server.infrastructure.database.database import DEFAULT_DB_PATH, Database
from server.infrastructure.logging.json_logging import configure_json_logging
from server.presentation.http_api import DEFAULT_HTTP_PORT, HttpApi, HttpApiServer
from server.presentation.ws_server import DEFAULT_HOST, DEFAULT_PORT, KFChessServer

_LOGGER = logging.getLogger(__name__)

ENV_TOKEN_SIGNING_KEY = "KFCHESS_TOKEN_SIGNING_KEY"
ENV_PREVIOUS_TOKEN_KEYS = "KFCHESS_PREVIOUS_TOKEN_KEYS"
ENV_TRUSTED_PROXIES = "KFCHESS_TRUSTED_PROXIES"

_LIST_SEPARATOR = ","

DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_CHOICES = ("DEBUG", "INFO", "WARNING", "ERROR")

READINESS_CHECK_DATABASE = "database"

ARG_HOST = "--host"
ARG_PORT = "--port"
ARG_HTTP_PORT = "--http-port"
ARG_DB_PATH = "--db-path"
ARG_LOG_LEVEL = "--log-level"
ARG_TLS_CERT = "--tls-cert"
ARG_TLS_KEY = "--tls-key"


@dataclass
class ServerSettings:
    """Everything a server process needs to know before it builds anything."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    http_port: int = DEFAULT_HTTP_PORT
    db_path: str = DEFAULT_DB_PATH
    log_level: str = DEFAULT_LOG_LEVEL
    tls_cert: Optional[str] = None
    tls_key: Optional[str] = None
    token_signing_key: Optional[str] = None
    previous_token_keys: List[str] = field(default_factory=list)
    trusted_proxies: List[str] = field(default_factory=list)

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert and self.tls_key)


def build_arg_parser(description: str) -> argparse.ArgumentParser:
    """The argument surface shared by all three entry points.

    One parser for every role rather than one per role: the roles differ in
    which ports they bind, not in how they are configured, and a shared surface
    means a deployment can pass the same flags to whichever binary it runs.
    """
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(ARG_HOST, default=DEFAULT_HOST, help="Bind address")
    parser.add_argument(ARG_PORT, type=int, default=DEFAULT_PORT, help="WebSocket bind port")
    parser.add_argument(ARG_HTTP_PORT, type=int, default=DEFAULT_HTTP_PORT, help="HTTP API bind port")
    parser.add_argument(ARG_DB_PATH, default=DEFAULT_DB_PATH, help="SQLite database file path")
    parser.add_argument(ARG_LOG_LEVEL, default=DEFAULT_LOG_LEVEL, choices=list(LOG_LEVEL_CHOICES))
    parser.add_argument(
        ARG_TLS_CERT,
        default=None,
        help=(
            "PEM certificate. Production terminates TLS at the ingress; this is "
            "for reproducing a TLS problem locally without one."
        ),
    )
    parser.add_argument(ARG_TLS_KEY, default=None, help="PEM private key for --tls-cert")
    return parser


def settings_from_args(args: argparse.Namespace) -> ServerSettings:
    """Combine parsed arguments with the environment-supplied secrets.

    The signing key arrives through the environment (a Kubernetes Secret in a
    real deployment) and never through a flag, because a flag is visible in the
    process table to every other process on the host.
    """
    return ServerSettings(
        host=args.host,
        port=args.port,
        http_port=args.http_port,
        db_path=args.db_path,
        log_level=args.log_level,
        tls_cert=args.tls_cert,
        tls_key=args.tls_key,
        token_signing_key=os.environ.get(ENV_TOKEN_SIGNING_KEY) or None,
        previous_token_keys=_split_env(ENV_PREVIOUS_TOKEN_KEYS),
        trusted_proxies=_split_env(ENV_TRUSTED_PROXIES),
    )


def _split_env(name: str) -> List[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(_LIST_SEPARATOR) if item.strip()]


def build_ssl_context(settings: ServerSettings) -> Optional[ssl.SSLContext]:
    """A server TLS context, or None when TLS terminates upstream.

    Returning None is the normal production path: the ingress holds the
    certificate and traffic inside the cluster is plaintext on a private
    network, which keeps certificate rotation out of this process's lifecycle
    and off the event loop that runs every game.
    """
    if not settings.tls_enabled:
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(settings.tls_cert, settings.tls_key)
    return context


def build_token_service(settings: ServerSettings) -> Optional[TokenService]:
    """A token service, or None when no signing key is configured.

    None rather than a generated key: a per-process random key would make every
    token invalid the moment a replica restarts or a second replica answers, and
    would look like it worked in a single-process test.
    """
    if not settings.token_signing_key:
        _LOGGER.warning(
            "%s is not set; token authentication is disabled and every socket "
            "will re-verify a password",
            ENV_TOKEN_SIGNING_KEY,
        )
        return None
    return TokenService(
        signing_key=settings.token_signing_key,
        previous_keys=settings.previous_token_keys,
    )


def build_readiness(database: Database) -> ReadinessProbe:
    """A probe that reports ready only while the database answers."""
    probe = ReadinessProbe()
    probe.register(READINESS_CHECK_DATABASE, database.ping)
    return probe


def build_http_api(
    database: Database,
    settings: ServerSettings,
    readiness: ReadinessProbe,
    token_service: Optional[TokenService],
) -> HttpApi:
    return HttpApi(
        query_service=GameQueryService(database),
        readiness=readiness,
        auth_service=AuthService(database),
        token_service=token_service,
        trusted_proxies=settings.trusted_proxies,
    )


async def run_api(settings: ServerSettings) -> None:
    """Run the HTTP API alone: history reads, leaderboard, and token issuance."""
    async with _database(settings) as database:
        readiness = build_readiness(database)
        api = build_http_api(database, settings, readiness, build_token_service(settings))
        server = HttpApiServer(
            api=api,
            host=settings.host,
            port=settings.http_port,
            ssl_context=build_ssl_context(settings),
        )
        await server.start()
        try:
            await _serve_until_signalled(readiness)
        finally:
            await server.stop()


async def run_ws(settings: ServerSettings) -> None:
    """Run the WebSocket game server alone.

    It still opens a database, because password auth and ELO persistence live
    behind it. Once clients present tokens issued by the API tier, the only
    remaining reads here are rating writes — which Step 8 moves onto a
    persistence worker, leaving this tier stateless.
    """
    async with _database(settings) as database:
        server = KFChessServer(
            host=settings.host,
            port=settings.port,
            database=database,
            auth_service=AuthService(database),
            token_service=build_token_service(settings),
            ssl_context=build_ssl_context(settings),
            trusted_proxies=settings.trusted_proxies,
        )
        await server.start()
        try:
            await _serve_until_signalled(build_readiness(database))
        finally:
            await server.stop()


async def run_combined(settings: ServerSettings) -> None:
    """Run both roles on one loop — the local-development topology.

    Convenient on one machine and wrong in a fleet: a replay query storm here
    competes for the same event loop that forwards moves.
    """
    async with _database(settings) as database:
        readiness = build_readiness(database)
        token_service = build_token_service(settings)
        ssl_context = build_ssl_context(settings)

        ws_server = KFChessServer(
            host=settings.host,
            port=settings.port,
            database=database,
            auth_service=AuthService(database),
            token_service=token_service,
            ssl_context=ssl_context,
            trusted_proxies=settings.trusted_proxies,
        )
        http_server = HttpApiServer(
            api=build_http_api(database, settings, readiness, token_service),
            host=settings.host,
            port=settings.http_port,
            ssl_context=ssl_context,
        )

        await http_server.start()
        await ws_server.start()
        try:
            await _serve_until_signalled(readiness)
        finally:
            await ws_server.stop()
            await http_server.stop()


class _database:
    """Async context manager owning one Database connection for a process run."""

    def __init__(self, settings: ServerSettings) -> None:
        self._settings = settings
        self._database: Optional[Database] = None

    async def __aenter__(self) -> Database:
        configure_json_logging(self._settings.log_level)
        self._database = Database(self._settings.db_path)
        await self._database.connect()
        return self._database

    async def __aexit__(self, *_exc_info) -> None:
        if self._database is not None:
            await self._database.close()
            self._database = None


async def _serve_until_signalled(readiness: ReadinessProbe) -> None:
    """Block until SIGTERM/SIGINT, then drain rather than exiting immediately.

    Draining means reporting not-ready while staying alive: the load balancer
    stops sending new work, and the games already running on this replica are
    allowed to finish. Exiting the moment the signal arrives would disconnect
    every player mid-game, which is the behaviour Step 7's `preStop` hook exists
    to prevent.
    """
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    await stop_event.wait()
    readiness.begin_draining()
    _LOGGER.info("Shutdown signalled; draining")


def _install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Register termination handlers, tolerating platforms that lack them.

    `add_signal_handler` is not implemented on Windows' proactor loop, where a
    developer runs this in the foreground and interrupts it with Ctrl-C —
    KeyboardInterrupt then unwinds `asyncio.run` and the entry point's own
    handler takes over.
    """
    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError, ValueError):
            _LOGGER.debug("Signal %s is not installable on this platform", sig)


def run_entry_point(description: str, run, argv: Optional[Sequence[str]] = None) -> None:
    """Parse arguments and run one role until it stops.

    Shared by all three entry scripts so `main_ws.py` and `main_api.py` differ
    in exactly one thing: which coroutine they hand in.
    """
    settings = settings_from_args(build_arg_parser(description).parse_args(argv))
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        _LOGGER.info("Server stopped by user")
