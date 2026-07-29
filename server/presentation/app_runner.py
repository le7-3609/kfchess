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
import socket
import ssl
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence

from server.application.auth_service import AuthService
from server.application.game_query_service import GameQueryService
from server.application.game_persistence_service import GamePersistenceService
from server.application.gateway_relay import GatewayRelay
from server.application.health import ReadinessProbe
from server.application.persistence_worker import PersistenceWorker
from server.application.remote_seat import RemoteSeat
from server.application.room_command_listener import RoomCommandListener
from server.application.room_manager import RoomManager
from server.application.room_ownership import RoomOwnership
from server.application.token_service import TokenService
from server.domain.coordination.broker import InProcessBroker
from server.domain.coordination.leases import InProcessLeases
from server.domain.matchmaking.queue import MatchmakingQueue
from server.infrastructure.database.database import DEFAULT_DB_PATH, Database
from server.infrastructure.logging.json_logging import configure_json_logging
from server.infrastructure.observability.server_metrics import server_metrics
from server.presentation.http_api import DEFAULT_HTTP_PORT, HttpApi, HttpApiServer
from server.presentation.ws_server import DEFAULT_HOST, DEFAULT_PORT, KFChessServer

_LOGGER = logging.getLogger(__name__)

ENV_TOKEN_SIGNING_KEY = "KFCHESS_TOKEN_SIGNING_KEY"
ENV_PREVIOUS_TOKEN_KEYS = "KFCHESS_PREVIOUS_TOKEN_KEYS"
ENV_TRUSTED_PROXIES = "KFCHESS_TRUSTED_PROXIES"
ENV_REDIS_URL = "KFCHESS_REDIS_URL"
ENV_POSTGRES_DSN = "KFCHESS_POSTGRES_DSN"
ENV_INSTANCE_ID = "KFCHESS_INSTANCE_ID"
ENV_NATS_URL = "KFCHESS_NATS_URL"

_LIST_SEPARATOR = ","

DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_CHOICES = ("DEBUG", "INFO", "WARNING", "ERROR")

READINESS_CHECK_DATABASE = "database"
READINESS_CHECK_REDIS = "redis"
READINESS_CHECK_BROKER = "broker"

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
    redis_url: Optional[str] = None
    postgres_dsn: Optional[str] = None
    nats_url: Optional[str] = None
    instance_id: str = ""

    @property
    def tls_enabled(self) -> bool:
        return bool(self.tls_cert and self.tls_key)

    @property
    def shared_state_enabled(self) -> bool:
        """Whether this process coordinates through Redis rather than in RAM.

        The absence of a URL is a valid configuration, not a misconfiguration: a
        single process has nothing to share state *with*, and the in-process
        stores behind the same ports are what let the suite and a developer's
        machine run with no containers at all.
        """
        return bool(self.redis_url)


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
        redis_url=os.environ.get(ENV_REDIS_URL) or None,
        postgres_dsn=os.environ.get(ENV_POSTGRES_DSN) or None,
        nats_url=os.environ.get(ENV_NATS_URL) or None,
        instance_id=os.environ.get(ENV_INSTANCE_ID) or _derived_instance_id(),
    )


def _derived_instance_id() -> str:
    """A name for this process, for directory entries and ownership leases.

    Derived rather than required, because a deployment that forgets to set it
    must not end up with fifty replicas all calling themselves the same thing —
    which reads as one instance owning everything and makes lease handover
    silently wrong. The hostname is stable and unique inside a cluster (it is the
    Pod name); the uuid suffix keeps two processes on one host apart.
    """
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"


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


def build_database(settings: ServerSettings):
    """The persistence adapter this process runs against.

    A PostgreSQL DSN selects the fleet adapter; its absence selects SQLite. The
    two satisfy the same surface, so nothing downstream branches on which one it
    got — which is the point of doing the choosing here and only here.
    """
    if settings.postgres_dsn:
        from server.infrastructure.database.postgres_database import PostgresDatabase

        _LOGGER.info("Using the PostgreSQL adapter")
        return PostgresDatabase(dsn=settings.postgres_dsn)
    return Database(settings.db_path)


def build_redis(settings: ServerSettings):
    """A Redis connection, or None when this process coordinates in RAM."""
    if not settings.shared_state_enabled:
        return None

    from server.infrastructure.coordination.redis_client import RedisConnection

    _LOGGER.info("Coordinating shared state through Redis as instance %s", settings.instance_id)
    return RedisConnection(url=settings.redis_url)


async def build_broker(settings: ServerSettings):
    """A connected broker, or the in-process one when no URL is configured.

    Never None. Unlike Redis — whose absence genuinely means "there is no fleet
    to coordinate with" — every deployment publishes room events somewhere, and
    the in-process implementation is a real destination rather than a null
    object. Callers therefore never branch on whether a broker exists.
    """
    if not settings.nats_url:
        return InProcessBroker()

    from server.infrastructure.broker.nats_broker import NatsBroker

    broker = NatsBroker(url=settings.nats_url)
    await broker.connect()
    return broker


def build_matchmaker(settings: ServerSettings, redis: Any, broker: Any) -> MatchmakingQueue:
    """The matchmaking queue, backed by Redis when the fleet shares one.

    The remote resolver is what completes the cross-replica pairing: a ticket
    naming a player whose socket is on another instance resolves to a
    `RemoteSeat`, which the room then treats as an ordinary seat and reaches
    through the broker.
    """
    if redis is None:
        return MatchmakingQueue(replica_id=settings.instance_id)

    from server.infrastructure.coordination.redis_queue_backend import RedisQueueBackend

    return MatchmakingQueue(
        backend=RedisQueueBackend(redis),
        replica_id=settings.instance_id,
        remote_resolver=_remote_seat_resolver(broker),
    )


def _remote_seat_resolver(broker: Any):
    def resolve(ticket) -> RemoteSeat:
        return RemoteSeat(
            username=ticket.username,
            user_id=ticket.user_id,
            elo=ticket.elo,
            broker=broker,
            replica=ticket.replica,
        )

    return resolve


def build_room_manager(
    settings: ServerSettings, database, redis: Any, broker: Any, relay: GatewayRelay
) -> RoomManager:
    """The room registry, publishing to a shared directory when there is one."""
    directory = None
    if redis is not None:
        from server.infrastructure.coordination.redis_directory import RedisDirectory

        directory = RedisDirectory(redis, replica_id=settings.instance_id)

    manager = RoomManager(
        database=database,
        seat_directory=directory,
        room_directory=directory,
        instance_id=settings.instance_id,
        broker=broker,
        relay=relay,
    )
    # Constructed after the manager because it routes into it, and handed back
    # in because the manager is what knows when a room starts and stops being
    # this instance's to answer for.
    manager.attach_command_listener(RoomCommandListener(broker, manager))
    return manager


def build_room_ownership(
    settings: ServerSettings, redis: Any, room_manager: RoomManager
) -> RoomOwnership:
    """This instance's claim on the rooms it computes.

    Always built, never None. A single process genuinely owns every room it
    creates, and `InProcessLeases` records that — including the fencing token —
    so the same acquire/renew/release path runs in the default configuration
    rather than only against Redis. A store that exists only in the fleet
    configuration is a store whose call sites are exercised only in the fleet.

    Wired in after the manager for the same reason the command listener is: the
    lease-lost callback routes back into it.
    """
    if redis is None:
        store: Any = InProcessLeases()
    else:
        from server.infrastructure.coordination.redis_leases import RedisLeaseStore

        store = RedisLeaseStore(redis)

    ownership = RoomOwnership(
        store=store,
        instance_id=settings.instance_id,
        on_lease_lost=room_manager.surrender_room,
    )
    room_manager.attach_ownership(ownership)
    # The game authority's scaling signal, and the only one that distinguishes
    # what this instance is running from what it is entitled to run.
    server_metrics().bind_rooms_owned(lambda: ownership.owned_room_count)
    return ownership


def build_readiness(database, redis: Any = None, broker: Any = None) -> ReadinessProbe:
    """A probe that reports ready only while every dependency answers.

    Redis joins the database here for the same reason the database is there: a
    replica that cannot reach the shared queue and directory can accept a socket
    but cannot match, seat or reconnect anyone on it, and should be drained
    rather than handed work.

    The broker joins them for a sharper version of the same reason. A gateway
    that reports ready before it can reach the broker is handed live connections
    it physically cannot serve — it will accept the socket, seat the player, and
    then deliver nothing, which is worse than refusing the connection.
    """
    probe = ReadinessProbe()
    probe.register(READINESS_CHECK_DATABASE, database.ping)
    if redis is not None:
        probe.register(READINESS_CHECK_REDIS, redis.ping)
    if broker is not None:
        probe.register(READINESS_CHECK_BROKER, broker.is_connected)
    return probe


def build_http_api(
    database,
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
    """Run the HTTP API alone: history reads, leaderboard, and token issuance.

    Opens no Redis: the API tier holds no seat, owns no room and reads no queue,
    so a connection here would be a dependency it never uses and a readiness
    check that could take it out of rotation for a service it does not need.
    """
    async with _dependencies(settings) as (database, _redis, _broker):
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

    With a Redis URL configured, its queue and room directory are the shared
    ones, which is what makes two of these one player population rather than two.
    """
    async with _dependencies(settings, with_redis=True, with_broker=True) as (
        database, redis, broker,
    ):
        readiness = build_readiness(database, redis, broker)
        relay = GatewayRelay(broker)
        room_manager = build_room_manager(settings, database, redis, broker, relay)
        ownership = build_room_ownership(settings, redis, room_manager)
        server = KFChessServer(
            host=settings.host,
            port=settings.port,
            database=database,
            auth_service=AuthService(database),
            matchmaker=build_matchmaker(settings, redis, broker),
            room_manager=room_manager,
            token_service=build_token_service(settings),
            ssl_context=build_ssl_context(settings),
            trusted_proxies=settings.trusted_proxies,
            relay=relay,
        )
        # The operational port. This role answers no HTTP client, but it must
        # still be probed and scraped: without it Kubernetes has no readiness
        # signal to drain on, no liveness signal to restart on, and the HPA has
        # no open-connection count to scale on — which is the only signal that
        # means anything for a tier whose cost is sockets rather than CPU.
        probes = _probe_server(settings, readiness)
        await probes.start()
        await ownership.start()
        await server.start()
        try:
            # Draining stops this instance taking new rooms while it keeps
            # renewing the ones it holds, so the games already running here are
            # allowed to finish inside the termination grace period.
            await _serve_until_signalled(readiness, on_drain=ownership.begin_draining)
        finally:
            await server.stop()
            # After the rooms, so their own releases land first; what remains
            # here is released explicitly rather than left to expire, which is
            # what makes a clean shutdown's ids re-placeable immediately.
            await ownership.stop()
            await relay.close()
            await probes.stop()


def _probe_server(settings: ServerSettings, readiness: ReadinessProbe) -> HttpApiServer:
    """The operational endpoints, for a role that serves no HTTP API.

    `/healthz`, `/readyz` and `/metrics` only — `HttpApi` registers the history
    and token routes only when given the collaborators that serve them, so a
    gateway or a worker advertises nothing it cannot answer. Every role having
    the same operational surface on the same port is what lets one set of
    Kubernetes manifests describe all of them.
    """
    return HttpApiServer(
        api=HttpApi(readiness=readiness),
        host=settings.host,
        port=settings.http_port,
    )


async def run_worker(settings: ServerSettings) -> None:
    """Run a persistence worker: drain the finished-game stream into the database.

    Serves its own HTTP port for probes and metrics, because an autoscaler
    cannot read a backlog it cannot scrape and Kubernetes cannot restart a
    process it cannot probe. That port serves no API routes — this role answers
    no client — but the operational surface is identical to every other role's,
    which is what lets one set of manifests describe all of them.
    """
    async with _dependencies(settings, with_broker=True) as (database, _redis, broker):
        readiness = build_readiness(database, broker=broker)
        worker = PersistenceWorker(broker, GamePersistenceService(database))
        server_metrics().bind_persistence_pending(lambda: worker.pending_count)

        probes = _probe_server(settings, readiness)
        await probes.start()
        await worker.start()
        try:
            await _serve_until_signalled(readiness)
        finally:
            # The worker stops before the probe server so its final flush
            # happens while the database is still open. Those games were already
            # acknowledged to the broker: nothing will redeliver them.
            await worker.stop()
            await probes.stop()


async def run_combined(settings: ServerSettings) -> None:
    """Run both roles on one loop — the local-development topology.

    Convenient on one machine and wrong in a fleet: a replay query storm here
    competes for the same event loop that forwards moves.
    """
    async with _dependencies(settings, with_redis=True, with_broker=True) as (
        database, redis, broker,
    ):
        readiness = build_readiness(database, redis, broker)
        token_service = build_token_service(settings)
        ssl_context = build_ssl_context(settings)
        relay = GatewayRelay(broker)
        room_manager = build_room_manager(settings, database, redis, broker, relay)
        ownership = build_room_ownership(settings, redis, room_manager)

        ws_server = KFChessServer(
            host=settings.host,
            port=settings.port,
            database=database,
            auth_service=AuthService(database),
            matchmaker=build_matchmaker(settings, redis, broker),
            room_manager=room_manager,
            token_service=token_service,
            ssl_context=ssl_context,
            trusted_proxies=settings.trusted_proxies,
            relay=relay,
        )
        http_server = HttpApiServer(
            api=build_http_api(database, settings, readiness, token_service),
            host=settings.host,
            port=settings.http_port,
            ssl_context=ssl_context,
        )

        await http_server.start()
        await ownership.start()
        await ws_server.start()
        try:
            await _serve_until_signalled(readiness, on_drain=ownership.begin_draining)
        finally:
            await ws_server.stop()
            await ownership.stop()
            await http_server.stop()


class _dependencies:
    """Async context manager owning this process's external connections.

    One place opens them and one place closes them, in reverse order, whichever
    role is running — so a role that gains a dependency does not also gain its
    own half-correct teardown.
    """

    def __init__(
        self, settings: ServerSettings, with_redis: bool = False, with_broker: bool = False
    ) -> None:
        self._settings = settings
        self._with_redis = with_redis
        self._with_broker = with_broker
        self._database = None
        self._redis = None
        self._broker = None

    async def __aenter__(self):
        configure_json_logging(self._settings.log_level)
        self._database = build_database(self._settings)
        await self._database.connect()
        if self._with_redis:
            self._redis = build_redis(self._settings)
        if self._with_broker:
            self._broker = await build_broker(self._settings)
        return (self._database, self._redis, self._broker)

    async def __aexit__(self, *_exc_info) -> None:
        if self._broker is not None and hasattr(self._broker, "close"):
            await self._broker.close()
            self._broker = None
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
        if self._database is not None:
            await self._database.close()
            self._database = None


async def _serve_until_signalled(
    readiness: ReadinessProbe, on_drain: Optional[Callable[[], None]] = None
) -> None:
    """Block until SIGTERM/SIGINT, then drain rather than exiting immediately.

    Draining means reporting not-ready while staying alive: the load balancer
    stops sending new work, and the games already running on this replica are
    allowed to finish. Exiting the moment the signal arrives would disconnect
    every player mid-game, which is the behaviour Step 7's `preStop` hook exists
    to prevent.

    *on_drain* is the role's own half of that — for the game authority, refusing
    new rooms while continuing to renew the leases it already holds. Readiness
    and ownership drain together deliberately: an instance that stopped taking
    rooms while still advertising itself as ready would be handed players it
    would immediately refuse.
    """
    stop_event = asyncio.Event()
    _install_signal_handlers(stop_event)
    await stop_event.wait()
    readiness.begin_draining()
    if on_drain is not None:
        on_drain()
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
