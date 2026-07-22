"""WebSocket server — accepts client connections and routes frames to use cases.

Layer: presentation (server/presentation)
Owns: WebSocket lifecycle, the auth retry loop, frame parsing, and the dispatch
table mapping a frame type onto an application use case. Turns a use case's
failed Result into an `error` frame for the requester.
Must not own: authentication policy (AuthUseCase), pairing and seating
(MatchmakingUseCase / RoomUseCase), or in-game routing (GameSessionUseCase).
"""

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

try:
    import websockets
except ImportError:
    websockets = None  # type: ignore[assignment]

from server.application.auth_use_case import AuthUseCase
from server.application.dtos import Identity
from server.application.game_session_use_case import GameSessionUseCase
from server.application.matchmaking_use_case import MatchmakingUseCase
from server.application.room_use_case import RoomUseCase
from server.application.auth_service import AuthService
from server.domain.matchmaking.queue import MatchmakingQueue
from server.domain.room.room_role import RoomRole
from server.infrastructure.database.database import Database
from server.infrastructure.services.bot_driver import DEFAULT_BOT_MOVE_INTERVAL_SECONDS
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
    ) -> None:
        self._host = host
        self._port = port
        self._database = database
        self._auth_service = auth_service
        self._server = None
        self._sessions: Dict[Any, PlayerSession] = {}

        # Injectable so tests can shrink the 60s queue timeout without waiting
        # a real minute for the bot-fallback path.
        self._matchmaker = matchmaker or MatchmakingQueue()
        self._room_manager = room_manager or RoomManager(database=database)
        self._matchmaking_poll_interval = matchmaking_poll_interval
        self._matchmaking_task: Optional[asyncio.Task] = None

        self._auth = AuthUseCase(auth_service)
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
        }

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

    async def start(self) -> None:
        """Start accepting connections and begin polling the matchmaking queue."""
        if websockets is None:
            raise RuntimeError("websockets package is not installed")

        self._server = await websockets.serve(self._handle_connection, self._host, self._port)
        self._matchmaking_task = asyncio.ensure_future(self._matchmaking_loop())
        _LOGGER.info("KungFu Chess server running on ws://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        """Stop matchmaking, tear down every live room, and close the listener."""
        await self._stop_matchmaking_loop()

        for room in self._room_manager.all_rooms():
            await room.stop()
            self._room_manager.remove_room(room.room_id)

        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
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
        identity = await self._authenticate(websocket)
        if identity is None:
            return

        handshake = await self._read_handshake(websocket)
        if handshake is None:
            return

        session = await self._establish_session(websocket, handshake, identity)
        if session is None:
            return

        try:
            async for raw_msg in websocket:
                await self._process_message(session, raw_msg)
        except Exception as exc:
            _LOGGER.debug("Connection ended for %s: %s", session.username, exc)
        finally:
            await self._game.handle_connection_closed(session)
            self._sessions.pop(websocket, None)

    async def _establish_session(
        self, websocket: Any, handshake: Dict[str, Any], identity: Identity
    ) -> Optional[PlayerSession]:
        """Turn the post-auth handshake frame into a registered, routable session."""
        user_id, username, elo = identity

        if handshake.get(FIELD_TYPE) == MSG_RECONNECT:
            session = await self._handle_reconnect_handshake(websocket, handshake, username)
            if session is None:
                return None
            self._sessions[websocket] = session
            return session

        session = PlayerSession(websocket=websocket, username=username, user_id=user_id, elo=elo)
        _LOGGER.info("New connection: %s (id=%d)", username, user_id)

        # Registered before dispatch so a handshake that pairs instantly (a
        # second player already queued) finds a fully-tracked session.
        self._sessions[websocket] = session
        await self._dispatch_message(session, handshake)
        return session

    async def _authenticate(self, websocket: Any) -> Optional[Identity]:
        """Run the mandatory auth handshake, allowing retries on bad credentials.

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

            result = await self._auth.authenticate(frame)
            if result.is_ok:
                _, resolved_username, elo = result.value
                await self._safe_send(websocket, build_auth_success_message(resolved_username, elo))
                return result.value

            await self._safe_send(websocket, build_error_message(result.error))
            if attempt == MAX_AUTH_ATTEMPTS:
                await self._safe_send(
                    websocket, build_error_message("Too many failed authentication attempts")
                )
                return None

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
        await self._reply_on_failure(session, await self._game.submit_move(session, msg))

    async def _handle_ping(self, session: PlayerSession, msg: Dict[str, Any]) -> None:
        await session.send({FIELD_TYPE: MSG_PONG})

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
        try:
            msg = parse_client_message(raw_msg)
        except ValueError as err:
            await session.send(build_error_message(str(err)))
            return

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
