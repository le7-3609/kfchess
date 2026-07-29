"""WebSocket server — accepts client connections and routes frames to use cases.

Layer: presentation (server/presentation)
Owns: WebSocket lifecycle, the auth retry loop, frame parsing, admission
control at the edge, heartbeat registration, and the dispatch table mapping a
frame type onto an application use case. Turns a use case's failed Result into
an `error` frame for the requester.
Must not own: authentication policy (AuthUseCase), the rate-limit budgets
themselves (ConnectionGuard), pairing and seating (MatchmakingUseCase /
RoomUseCase), or in-game routing (GameSessionUseCase).
"""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]

from server.application.auth_use_case import AuthUseCase
from server.application.dtos import Identity
from server.application.game_room import DEFAULT_RECONCILIATION_INTERVAL_SECONDS
from server.application.gateway_relay import GatewayRelay
from server.application.game_session_use_case import GameSessionUseCase
from server.application.matchmaking_use_case import MatchmakingUseCase
from server.application.room_use_case import RoomUseCase
from server.application.auth_service import AuthService
from server.application.token_service import TokenService
from server.domain.matchmaking.queue import MatchmakingQueue
from server.domain.room.room_role import RoomRole
from server.infrastructure.database.database import Database
from server.infrastructure.logging.json_logging import bind_session
from server.infrastructure.observability.server_metrics import ServerMetrics, server_metrics
from server.infrastructure.services.bot_driver import DEFAULT_BOT_MOVE_INTERVAL_SECONDS
from server.infrastructure.services.heartbeat import (
    DEFAULT_PING_INTERVAL_SECONDS,
    DEFAULT_PONG_TIMEOUT_SECONDS,
    HeartbeatMonitor,
)
from server.presentation.connection_guard import ConnectionGuard
from server.application.dtos.frame_fields import (
    FIELD_MESSAGE,
    FIELD_TYPE,
    FIELD_USERNAME,
)
from server.application.dtos.network_frames import (
    MSG_AUTH,
    MSG_CANCEL_SEARCH,
    MSG_CREATE_ROOM,
    MSG_INFO,
    MSG_JOIN_ROOM,
    MSG_MOVE,
    MSG_PING,
    MSG_PLAY,
    MSG_PONG,
    MSG_RECONNECT,
)
from server.application.dtos.response_frames import (
    build_auth_success_message,
    build_error_message,
    build_room_created_message,
)
from server.application.dtos.protocol_mapper import parse_client_message
from server.presentation.ws_connection import PlayerSession
from server.application.room_manager import RoomManager

_LOGGER = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8765
DEFAULT_MATCHMAKING_POLL_INTERVAL_SECONDS = 0.1

MAX_AUTH_ATTEMPTS = 3


class KFChessServer:
    """Core WebSocket server for KungFu Chess multiplayer."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        database: Optional[Database] = None,
        auth_service: Optional[AuthService] = None,
        matchmaker: Optional[MatchmakingQueue] = None,
        room_manager: Optional[RoomManager] = None,
        matchmaking_poll_interval: float = DEFAULT_MATCHMAKING_POLL_INTERVAL_SECONDS,
        bot_move_interval_seconds: float = DEFAULT_BOT_MOVE_INTERVAL_SECONDS,
        token_service: Optional[TokenService] = None,
        ssl_context: Optional[Any] = None,
        trusted_proxies: Sequence[str] = (),
        guard: Optional[ConnectionGuard] = None,
        heartbeat_interval_seconds: float = DEFAULT_PING_INTERVAL_SECONDS,
        heartbeat_timeout_seconds: float = DEFAULT_PONG_TIMEOUT_SECONDS,
        reconciliation_interval_seconds: float = DEFAULT_RECONCILIATION_INTERVAL_SECONDS,
        metrics: Optional[ServerMetrics] = None,
        relay: Optional[GatewayRelay] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._auth_service = auth_service
        self._ssl_context = ssl_context
        self._server = None
        self._sessions: Dict[Any, PlayerSession] = {}
        # Present only when a broker is configured. It is what makes this
        # process a gateway for rooms it does not own: without it, every seat a
        # client holds must be in a room on this same process.
        self._relay = relay

        # Injectable so tests can shrink the 60s queue timeout without waiting
        # a real minute for the bot-fallback path.
        self._matchmaker = matchmaker or MatchmakingQueue()
        self._room_manager = room_manager or RoomManager(
            database=database,
            reconciliation_interval_seconds=reconciliation_interval_seconds,
        )
        self._matchmaking_poll_interval = matchmaking_poll_interval
        self._matchmaking_task: Optional[asyncio.Task] = None

        self._guard = guard or ConnectionGuard(trusted_proxies=trusted_proxies)
        self._metrics = metrics or server_metrics()
        self._bind_metric_sources()

        # A socket whose network vanishes without a TCP FIN is otherwise noticed
        # only when a write raises — and PlayerSession.send returns silently
        # when the session is not connected, so it may never be noticed at all,
        # leaving the seat occupied until the game ends on its own.
        self._heartbeat = HeartbeatMonitor(
            ping_interval=heartbeat_interval_seconds,
            pong_timeout=heartbeat_timeout_seconds,
            on_disconnect=self._on_heartbeat_timeout,
        )

        self._auth = AuthUseCase(auth_service, token_service)
        self._matchmaking = MatchmakingUseCase(
            matchmaker=self._matchmaker,
            room_manager=self._room_manager,
            bot_move_interval_seconds=bot_move_interval_seconds,
        )
        self._rooms = RoomUseCase(room_manager=self._room_manager, matchmaker=self._matchmaker)
        self._game = GameSessionUseCase(
            room_manager=self._room_manager, matchmaker=self._matchmaker
        )

        # Frame-type dispatch table, declared once. A new lobby action means a
        # new row here rather than another branch in a growing if/elif chain.
        # Every handler takes (session, msg) so they share one call shape, even
        # where the message body is unused.
        self._message_handlers: Dict[
            str, Callable[[PlayerSession, Dict[str, Any]], Awaitable[None]]
        ] = {
            MSG_PLAY: lambda session, msg: self._handle_play(session),
            MSG_CANCEL_SEARCH: lambda session, msg: self._handle_cancel_search(session),
            MSG_CREATE_ROOM: lambda session, msg: self._handle_create_room(session),
            MSG_JOIN_ROOM: self._handle_join_room,
            MSG_MOVE: self._handle_move,
            MSG_PING: self._handle_ping,
            MSG_PONG: self._handle_pong,
        }

    def _bind_metric_sources(self) -> None:
        """Expose the live registries a scrape reads through.

        Bound to accessors rather than pushed on change: every one of these
        numbers already exists on an object that maintains it, and mirroring
        them into a metric on every mutation is a second source of truth that
        can only ever drift.
        """
        self._metrics.bind_rooms_active(lambda: self._room_manager.room_count)
        self._metrics.bind_sessions_active(lambda: len(self._sessions))
        self._metrics.bind_queue_length(lambda: self._matchmaker.queue_length)
        self._metrics.bind_countdowns_active(self._room_manager.active_countdown_count)

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def room_manager(self) -> RoomManager:
        return self._room_manager

    @property
    def matchmaker(self) -> MatchmakingQueue:
        return self._matchmaker

    @property
    def heartbeat(self) -> HeartbeatMonitor:
        return self._heartbeat

    @property
    def session_count(self) -> int:
        return len(self._sessions)

    async def start(self) -> None:
        """Start accepting connections and begin polling the matchmaking queue."""
        if websockets is None:
            raise RuntimeError("websockets package is not installed")

        self._server = await websockets.serve(
            self._handle_connection, self._host, self._port, ssl=self._ssl_context
        )
        self._matchmaking_task = asyncio.ensure_future(self._matchmaking_loop())
        await self._heartbeat.start()
        _LOGGER.info(
            "KungFu Chess server running on %s://%s:%d",
            "wss" if self._ssl_context is not None else "ws",
            self._host,
            self._port,
        )

    async def stop(self) -> None:
        """Stop matchmaking, tear down every live room, and close the listener."""
        await self._stop_matchmaking_loop()
        await self._heartbeat.stop()

        for room in self._room_manager.all_rooms():
            await room.stop()
            self._room_manager.remove_room(room.room_id)

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        # The source-backed gauges read this server's registries; leaving them
        # bound after teardown means a later scrape reports a stopped server's
        # objects as if they were live.
        self._metrics.unbind_live_gauges()
        _LOGGER.info("Server stopped")

    async def _stop_matchmaking_loop(self) -> None:
        if self._matchmaking_task is None:
            return
        self._matchmaking_task.cancel()
        try:
            await self._matchmaking_task
        except asyncio.CancelledError:
            pass
        finally:
            self._matchmaking_task = None

    async def run_forever(self) -> None:
        """Start server and block until cancelled."""
        await self.start()
        stop_event = asyncio.Event()
        await stop_event.wait()

    async def _matchmaking_loop(self) -> None:
        """Poll the queue, pairing compatible players and rescuing timed-out ones.

        One iteration's failure must not end matchmaking for the whole server,
        so exceptions are logged and the loop continues.
        """
        try:
            while True:
                await asyncio.sleep(self._matchmaking_poll_interval)
                try:
                    await self._matchmaking.drain_matches()
                    await self._matchmaking.drain_timeouts()
                except Exception as exc:
                    _LOGGER.exception("Matchmaking iteration failed: %s", exc)
        except asyncio.CancelledError:
            pass

    async def _handle_connection(self, websocket: Any) -> None:
        """Handle lifecycle of a single WebSocket connection.

        Every connection must open with an `{"type": "auth"}` handshake
        (login or register) before it may reach any room or lobby state.
        Only once an authenticated identity is established does the second
        frame route the socket: `{"type": "reconnect"}` rebinds a client
        riding out its backoff window onto its existing seat, while anything
        else is dispatched through the ordinary lobby router — so `play`,
        `create_room` and `join_room` behave identically whether they arrive
        as that first frame or later on an idle connection.
        """
        address = self._guard.client_address(websocket)
        admission = self._guard.admit_connection(address)
        if not admission.allowed:
            self._metrics.connections_rejected.increment()
            await self._safe_send(websocket, build_error_message(admission.reason))
            await self._close_quietly(websocket)
            return

        identity = await self._authenticate(websocket)
        if identity is None:
            return

        handshake = await self._read_handshake(websocket)
        if handshake is None:
            return

        session = await self._establish_session(websocket, handshake, identity)
        if session is None:
            return

        self._heartbeat.register_session(session)
        try:
            async for raw_msg in websocket:
                await self._process_message(session, raw_msg)
        except Exception as exc:
            _LOGGER.debug("Connection ended for %s: %s", session.username, exc)
        finally:
            self._heartbeat.unregister_session(session)
            self._guard.forget_session(self._session_key(session))
            await self._game.handle_connection_closed(session)
            await self._detach_relay(session)
            self._sessions.pop(websocket, None)

    async def _attach_relay(self, session: PlayerSession) -> None:
        """Subscribe this socket to the frames addressed to its player.

        Opened as soon as the session exists rather than when it is seated,
        because the subject also carries the `game_start` frame that tells the
        client it *has* a room — subscribing after that would miss it.
        """
        if self._relay is None:
            return
        await self._relay.attach_session(session)

    async def _detach_relay(self, session: PlayerSession) -> None:
        if self._relay is None:
            return
        await self._relay.detach_session(session)

    def _on_heartbeat_timeout(self, session: Any) -> None:
        """Close a socket that stopped answering pings.

        Called synchronously from inside the heartbeat sweep, so the close is
        deferred onto its own task: `_handle_connection`'s `finally` block is
        the one path that frees a seat, and it only runs once the socket
        actually closes.
        """
        _LOGGER.warning("Closing unresponsive session for %s", getattr(session, "username", "?"))
        try:
            asyncio.get_running_loop().create_task(self._close_quietly(session.websocket))
        except RuntimeError:
            _LOGGER.debug("No running loop to close the unresponsive socket on")

    @staticmethod
    async def _close_quietly(websocket: Any) -> None:
        """Close a socket, tolerating one that is already gone."""
        try:
            await websocket.close()
        except Exception as exc:
            _LOGGER.debug("Closing a socket raised: %s", exc)

    @staticmethod
    def _session_key(session: PlayerSession) -> str:
        """The identity a per-socket frame budget is keyed by.

        The username, not the socket object: a client that reconnects in a loop
        to shed its own rate limit would otherwise get a fresh budget on every
        attempt.
        """
        return session.username

    async def _establish_session(
        self, websocket: Any, handshake: Dict[str, Any], identity: Identity
    ) -> Optional[PlayerSession]:
        """Turn the post-auth handshake frame into a registered, routable session."""
        user_id, username, elo = identity

        # Stamped onto every log record this connection's task emits from here
        # on, so a game can be traced across processes by field rather than by
        # reading it out of a message.
        bind_session(user_id, username)

        if handshake.get(FIELD_TYPE) == MSG_RECONNECT:
            session = await self._handle_reconnect_handshake(websocket, handshake, username)
            if session is None:
                return None
            self._sessions[websocket] = session
            await self._attach_relay(session)
            return session

        session = PlayerSession(websocket=websocket, username=username, user_id=user_id, elo=elo)
        _LOGGER.info("New connection: %s (id=%d)", username, user_id)

        # Registered and relayed before dispatch so a handshake that pairs
        # instantly (a second player already queued) finds a fully-tracked
        # session with its subscriptions already live — otherwise the
        # `game_start` frame it triggers can be published before anything is
        # listening for it.
        self._sessions[websocket] = session
        await self._attach_relay(session)
        await self._dispatch_message(session, handshake)
        return session

    async def _authenticate(self, websocket: Any) -> Optional[Identity]:
        """Run the mandatory auth handshake, allowing retries on bad credentials.

        The three-attempt budget is per connection, which on its own bounds
        nothing — connections are cheap to reopen. The per-username backoff
        applied here is what actually slows a guess, and it survives the
        attacker reconnecting because it is keyed by the account under attack
        rather than by the socket.

        Returns the authenticated (user_id, username, elo) identity, or None
        if the socket dropped, sent a malformed/non-auth first frame, or
        exhausted its retry budget on invalid credentials.
        """
        for attempt in range(1, MAX_AUTH_ATTEMPTS + 1):
            frame = await self._read_handshake(websocket)
            if frame is None:
                return None

            if frame.get(FIELD_TYPE) != MSG_AUTH:
                await self._safe_send(
                    websocket, build_error_message("First message must be an 'auth' handshake")
                )
                return None

            identity = await self._attempt_authentication(websocket, frame)
            if identity is not None:
                return identity

            if attempt == MAX_AUTH_ATTEMPTS:
                await self._safe_send(
                    websocket, build_error_message("Too many failed authentication attempts")
                )
                return None

        return None

    async def _attempt_authentication(
        self, websocket: Any, frame: Dict[str, Any]
    ) -> Optional[Identity]:
        """Resolve one auth frame, applying and updating the per-username backoff.

        Returns the identity on success, or None after answering the refusal —
        the caller owns the retry budget.
        """
        claimed_username = frame.get(FIELD_USERNAME)
        is_password_attempt = not self._auth.is_token_attempt(frame)

        # Backoff guards credential guessing only. A token either verifies or
        # does not, and delaying its retry would punish a client whose access
        # token merely expired.
        if is_password_attempt and isinstance(claimed_username, str):
            backoff = self._guard.check_login_backoff(claimed_username)
            if not backoff.allowed:
                await self._safe_send(websocket, build_error_message(backoff.reason))
                return None

        result = await self._auth.authenticate(frame)
        if result.is_ok:
            _, resolved_username, elo = result.value
            self._guard.record_login_success(resolved_username)
            await self._safe_send(websocket, build_auth_success_message(resolved_username, elo))
            return result.value

        self._metrics.auth_failures.increment()
        if is_password_attempt and isinstance(claimed_username, str):
            self._guard.record_login_failure(claimed_username)
        await self._safe_send(websocket, build_error_message(result.error))
        return None

    async def _read_handshake(self, websocket: Any) -> Optional[Dict[str, Any]]:
        """Receive and parse the connection's first frame.

        Returns None (and closes out the caller) if the socket dropped before
        sending anything, or sent something that isn't a valid message.
        """
        try:
            raw = await websocket.recv()
        except Exception as exc:
            _LOGGER.debug("Connection closed before handshake: %s", exc)
            return None

        try:
            return parse_client_message(raw)
        except ValueError as err:
            await self._safe_send(websocket, build_error_message(str(err)))
            return None

    async def _handle_play(self, session: PlayerSession) -> None:
        await self._reply_on_failure(session, await self._matchmaking.enqueue(session))

    async def _handle_cancel_search(self, session: PlayerSession) -> None:
        await self._reply_on_failure(session, await self._matchmaking.cancel(session))

    async def _handle_create_room(self, session: PlayerSession) -> None:
        admission = self._guard.admit_room_creation(
            session.username, self._room_manager.count_rooms_hosted_by(session.username)
        )
        if not admission.allowed:
            await session.send(build_error_message(admission.reason))
            return

        result = await self._rooms.create(session)
        if not result.is_ok:
            await session.send(build_error_message(result.error))
            return
        await session.send(build_room_created_message(result.value))

    async def _handle_join_room(self, session: PlayerSession, msg: Dict[str, Any]) -> None:
        result = await self._rooms.join(session, msg)
        if not result.is_ok:
            await session.send(build_error_message(result.error))
            return
        if result.value == RoomRole.VIEWER:
            await session.send({FIELD_TYPE: MSG_INFO, FIELD_MESSAGE: "Joined room as spectator"})

    async def _handle_move(self, session: PlayerSession, msg: Dict[str, Any]) -> None:
        """Answer a move locally, or forward it to the instance that owns the room.

        The local path is tried first and costs nothing extra: a gateway that
        also owns the room — the common case for the player who created it —
        executes the move inline rather than taking a broker round trip to
        itself.
        """
        if await self._forward_move_to_owner(session, msg):
            return
        await self._reply_on_failure(session, await self._game.submit_move(session, msg))

    async def _forward_move_to_owner(
        self, session: PlayerSession, msg: Dict[str, Any]
    ) -> bool:
        """Publish a move for a room this process does not run; True if it did.

        Deliberately does not wait for a result. The authority answers a
        rejected move on the player's own frame subject, which arrives back
        through the relay like every other frame — so there is one inbound path
        for the client rather than two that could disagree.
        """
        if self._relay is None or self._room_manager.find_room_by_session(session) is not None:
            return False

        location = await self._room_manager.locate_seat(session.username)
        if location is None or location.is_on(self._room_manager.instance_id):
            return False

        await self._relay.join_room(location.room_id, session.username)
        await self._relay.forward_command(location.room_id, session.username, msg)
        return True

    async def _handle_ping(self, session: PlayerSession, msg: Dict[str, Any]) -> None:
        await session.send({FIELD_TYPE: MSG_PONG})

    async def _handle_pong(self, session: PlayerSession, msg: Dict[str, Any]) -> None:
        """Record the client's answer to a server ping, keeping the session alive."""
        self._heartbeat.record_pong(session)

    async def _handle_reconnect_handshake(
        self, websocket: Any, handshake: Dict[str, Any], authenticated_username: str
    ) -> Optional[PlayerSession]:
        """Rebind a returning client onto its existing seat, or refuse over the socket.

        The refusal goes out through `_safe_send` rather than the session,
        because on this path there is no session yet to send through.
        """
        result = await self._game.reconnect(
            authenticated_username, handshake.get(FIELD_USERNAME), websocket
        )
        if not result.is_ok:
            await self._safe_send(websocket, build_error_message(result.error))
            return None
        return result.value

    @staticmethod
    async def _reply_on_failure(session: PlayerSession, result: Any) -> None:
        """Answer a rejected request; a successful one is acknowledged elsewhere or not at all."""
        if not result.is_ok:
            await session.send(build_error_message(result.error))

    async def _safe_send(self, websocket: Any, message: Dict[str, Any]) -> None:
        try:
            await websocket.send(json.dumps(message))
        except Exception as exc:
            _LOGGER.debug("Failed to send handshake response: %s", exc)

    async def _process_message(self, session: PlayerSession, raw_msg: str) -> None:
        """Parse and dispatch one inbound frame, subject to the session's budget.

        The budget is applied before parsing so a flood costs the server a
        counter increment rather than a JSON decode, and over-limit frames are
        answered and dropped rather than closing the socket — a burst is far
        more often a retry storm from a bad network than an attack.
        """
        admission = self._guard.admit_frame(self._session_key(session))
        if not admission.allowed:
            self._metrics.frames_rate_limited.increment()
            await session.send(build_error_message(admission.reason))
            return

        try:
            msg = parse_client_message(raw_msg)
        except ValueError as err:
            await session.send(build_error_message(str(err)))
            return

        self._metrics.frames_received.increment()
        await self._dispatch_message(session, msg)

    async def _dispatch_message(self, session: PlayerSession, msg: Dict[str, Any]) -> None:
        """Route one parsed frame to its handler.

        Shared by the post-auth handshake frame and the steady-state message
        loop, so a lobby action means the same thing whenever it arrives.
        """
        msg_type = msg.get(FIELD_TYPE)
        handler = self._message_handlers.get(msg_type)
        if handler is None:
            await session.send(build_error_message(f"Unhandled message type: {msg_type!r}"))
            return
        await handler(session, msg)
