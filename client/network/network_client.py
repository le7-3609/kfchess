"""Persistent WebSocket bridge between the Tk GUI and the multiplayer server (client layer).

Owns: the background asyncio event loop and its daemon thread, the long-lived
      connection to server/presentation/ws_server.py, translating GUI actions into wire
      frames (and wire frames back into plain dicts for the GUI to consume),
      and the exponential-backoff reconnection loop that rides out a dropped
      connection inside the server's disconnect grace window. Every fresh
      socket — the first connection and each reconnect attempt alike — must
      open with its own `auth` handshake before `play`/`reconnect`, since the
      server authenticates per-connection rather than per-session; the
      credentials used are the ones already verified by the pre-GUI CLI login.
Must not own: GUI widgets/windows/state, game rules, or anything from the
      server package — this module may import shared/ and websockets only.
"""

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Dict, Optional

import websockets

from client.network.protocol import (
    AUTH_ACTION_LOGIN,
    FIELD_ACTION,
    FIELD_FROM,
    FIELD_PASSWORD,
    FIELD_ROOM_ID,
    FIELD_STATUS,
    FIELD_TO,
    FIELD_TYPE,
    FIELD_USERNAME,
    MSG_TYPE_AUTH,
    MSG_TYPE_CANCEL_SEARCH,
    MSG_TYPE_CREATE_ROOM,
    MSG_TYPE_ERROR,
    MSG_TYPE_JOIN_ROOM,
    MSG_TYPE_MOVE,
    MSG_TYPE_PLAY,
    MSG_TYPE_RECONNECT,
)

_LOGGER = logging.getLogger(__name__)

# Client-internal signal, synthesized locally and pushed through the same
# on_message_callback seam as wire frames — it is never sent to or received
# from the server, only used to drive the GUI's reconnect overlay.
MSG_TYPE_CONNECTION_STATUS = "connection_status"
STATUS_DISCONNECTED = "disconnected"
STATUS_RECONNECTING = "reconnecting"
STATUS_CONNECTED = "connected"
STATUS_RECONNECT_FAILED = "reconnect_failed"

# Extra fields the reconnecting status frame carries.
FIELD_ATTEMPT = "attempt"
FIELD_DELAY_SECONDS = "delay_seconds"

_THREAD_NAME = "NetworkClient-EventLoop"
_SHUTDOWN_TIMEOUT_SECONDS = 5.0

_INITIAL_BACKOFF_SECONDS = 1.0
_BACKOFF_MULTIPLIER = 2.0
_MAX_BACKOFF_SECONDS = 8.0
# Mirrors server.application.disconnect_handler.DEFAULT_DISCONNECT_TIMEOUT_SECONDS: once the
# server has declared a technical forfeit, further retries can't recover the match.
_RECONNECT_WINDOW_SECONDS = 30.0

OnMessageCallback = Callable[[Dict[str, Any]], None]


class NetworkClient:
    """Runs the game WebSocket connection on a dedicated background thread.

    Tkinter's main loop is synchronous and must never block on network I/O, so
    this class owns a daemon thread with its own asyncio event loop and talks
    to the GUI only through thread-safe seams: `start`/`stop`/`send_move` are
    called from the main thread, while `on_message_callback` (registered via
    `start`) is invoked *on the background thread* for every decoded frame,
    including the synthetic connection-status frames the reconnect loop emits.
    Callers that need to touch Tk state from the callback must marshal back to
    the main thread themselves (e.g. a queue drained on a `Tk.after` tick) —
    this class has no knowledge of Tkinter or any other GUI toolkit.
    """

    def __init__(
        self,
        server_url: str,
        username: str,
        password: str,
        initial_action: str = MSG_TYPE_PLAY,
        room_id: Optional[str] = None,
    ) -> None:
        self._server_url = server_url
        self._username = username
        self._password = password
        self._initial_action = initial_action
        self._room_id = room_id
        self._on_message: Optional[OnMessageCallback] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._main_task: Optional["asyncio.Task[None]"] = None
        self._connection: Optional[Any] = None
        self._closing = False
        self._reconnect_attempt = 0
        self._reconnect_elapsed_seconds = 0.0

    def set_action(self, action: str, room_id: Optional[str] = None) -> None:
        """Set the handshake action for the connection."""
        self._initial_action = action
        self._room_id = room_id

    def set_message_callback(self, on_message_callback: OnMessageCallback) -> None:
        """Redirect future frames to a new callback without restarting the connection.

        Lets a caller that started the client before its eventual consumer
        existed (the lobby opens the socket while still showing the
        matchmaking dialog, before the game window is built) hand delivery
        off once that consumer is ready.
        """
        self._on_message = on_message_callback

    def start(self, on_message_callback: OnMessageCallback) -> None:
        """Spawn the background event loop and open the server connection.

        `on_message_callback` is invoked once per decoded JSON frame, on the
        background thread.

        Raises:
            RuntimeError: If the client is already started.
        """
        if self._thread is not None:
            raise RuntimeError("NetworkClient is already started")

        self._on_message = on_message_callback
        self._closing = False
        self._thread = threading.Thread(target=self._run_event_loop, name=_THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Close the connection and shut down the background loop/thread cleanly.

        Cancels the listener task rather than calling `connection.close()` from
        a second task: the two concurrently awaiting the same connection can
        deadlock (the close handshake never observes completion while another
        task is parked in `recv()`). Cancellation unwinds whichever `async with
        websockets.connect(...)` block is currently active — the first
        connection or a reconnect attempt — which closes the socket as part of
        its own exit.
        """
        self._closing = True

        if self._loop is not None and self._main_task is not None:
            self._loop.call_soon_threadsafe(self._main_task.cancel)

        if self._thread is not None:
            self._thread.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)

        self._loop = None
        self._thread = None
        self._main_task = None

    def send_move(self, from_square: str, to_square: str) -> None:
        """Schedule an outbound move frame onto the background loop.

        Safe to call from the main GUI thread; the actual send happens on the
        background thread via `asyncio.run_coroutine_threadsafe`.
        """
        if self._loop is None:
            _LOGGER.warning("Dropping move %s->%s; network loop is not running", from_square, to_square)
            return

        payload = {FIELD_TYPE: MSG_TYPE_MOVE, FIELD_FROM: from_square, FIELD_TO: to_square}
        asyncio.run_coroutine_threadsafe(self._send(payload), self._loop)

    def send_cancel_search(self) -> None:
        """Schedule an outbound cancel_search frame onto the background loop.

        Safe to call from the main GUI thread, mirroring `send_move`; withdraws
        the player from matchmaking without waiting for a pairing or timeout.
        """
        if self._loop is None:
            _LOGGER.warning("Dropping cancel_search; network loop is not running")
            return

        payload = {FIELD_TYPE: MSG_TYPE_CANCEL_SEARCH}
        asyncio.run_coroutine_threadsafe(self._send(payload), self._loop)

    def _run_event_loop(self) -> None:
        """Thread entry point: own a fresh event loop for the lifetime of the connection."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        self._main_task = loop.create_task(self._connect_and_listen())
        try:
            loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass
        finally:
            # Close the loop via the local reference, not self._loop: stop()'s
            # join(timeout=...) can return before this thread actually exits,
            # after which stop() nulls self._loop from the main thread.
            loop.close()

    async def _connect_and_listen(self) -> None:
        """Open the connection, send the matchmaking payload, and stream frames in.

        A drop after that first success hands off to `_reconnect_loop`, which
        retries with exponential backoff inside the server's disconnect grace
        window; a failure on the very first attempt has no session to recover,
        so it is logged and left to end the task, as before.
        """
        if await self._open_session(MSG_TYPE_PLAY):
            return
        while await self._reconnect_loop():
            if await self._open_session(MSG_TYPE_RECONNECT):
                return

    async def _open_session(self, handshake_type: str) -> bool:
        """Connect, authenticate, send the handshake frame, and stream frames
        until the socket drops.

        Returns True once there is nothing left to recover — either the server
        closed the connection cleanly, or (for the very first attempt only,
        where there is no established session yet to preserve) the connection
        could not be made or authenticated at all. Returns False when a
        reconnect attempt should follow: the socket dropped mid-session, or a
        retry attempt itself failed to connect or authenticate.
        """
        try:
            async with websockets.connect(self._server_url) as connection:
                self._connection = connection
                if self._closing:
                    return True
                if not await self._authenticate(connection):
                    return handshake_type == MSG_TYPE_PLAY
                await connection.send(json.dumps(self._build_handshake(handshake_type)))
                if handshake_type == MSG_TYPE_RECONNECT:
                    self._on_reconnected()
                await self._listen(connection)
                return True
        except websockets.ConnectionClosed as exc:
            self._handle_disconnect(exc)
            return False
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            if handshake_type == MSG_TYPE_PLAY:
                _LOGGER.error("[%s] failed to connect to %s: %s", self._username, self._server_url, exc)
                return True
            _LOGGER.warning("[%s] reconnect attempt to %s failed: %s", self._username, self._server_url, exc)
            return False
        finally:
            self._connection = None

    async def _authenticate(self, connection: Any) -> bool:
        """Send the mandatory auth frame every fresh socket must open with.

        The server authenticates per-connection, not per-session, so this
        runs before every `play`/`reconnect` handshake — reusing the
        credentials the pre-GUI CLI login already verified rather than
        prompting the user again.
        """
        await connection.send(json.dumps(self._build_auth_frame()))
        raw_reply = await connection.recv()
        try:
            reply = json.loads(raw_reply)
        except (TypeError, json.JSONDecodeError):
            _LOGGER.error("[%s] malformed auth reply from server", self._username)
            return False

        if not isinstance(reply, dict) or reply.get(FIELD_TYPE) == MSG_TYPE_ERROR:
            _LOGGER.error("[%s] re-authentication failed: %s", self._username, reply)
            return False
        return True

    def _build_auth_frame(self) -> Dict[str, Any]:
        return {
            FIELD_TYPE: MSG_TYPE_AUTH,
            FIELD_ACTION: AUTH_ACTION_LOGIN,
            FIELD_USERNAME: self._username,
            FIELD_PASSWORD: self._password,
        }

    def _build_handshake(self, handshake_type: str) -> Dict[str, Any]:
        if handshake_type == MSG_TYPE_RECONNECT:
            return {FIELD_TYPE: MSG_TYPE_RECONNECT, FIELD_USERNAME: self._username}
        if self._initial_action == MSG_TYPE_CREATE_ROOM:
            return {FIELD_TYPE: MSG_TYPE_CREATE_ROOM}
        if self._initial_action == MSG_TYPE_JOIN_ROOM:
            return {FIELD_TYPE: MSG_TYPE_JOIN_ROOM, FIELD_ROOM_ID: self._room_id}
        return {FIELD_TYPE: MSG_TYPE_PLAY}

    def _on_reconnected(self) -> None:
        self._reconnect_attempt = 0
        self._reconnect_elapsed_seconds = 0.0
        _LOGGER.info("[%s] reconnected to %s", self._username, self._server_url)
        self._emit_status(STATUS_CONNECTED)

    async def _reconnect_loop(self) -> bool:
        """Wait out one exponential-backoff step before the next reconnect attempt.

        Returns False once `stop()` was requested or the retry budget
        (mirroring the server's disconnect window) is exhausted — in either
        case the caller gives up rather than attempting to reconnect again.
        """
        if self._closing:
            return False
        if self._reconnect_elapsed_seconds >= _RECONNECT_WINDOW_SECONDS:
            _LOGGER.error(
                "[%s] exhausted reconnection attempts to %s after %.0fs",
                self._username, self._server_url, self._reconnect_elapsed_seconds,
            )
            self._emit_status(STATUS_RECONNECT_FAILED)
            return False

        self._reconnect_attempt += 1
        delay = min(
            _INITIAL_BACKOFF_SECONDS * (_BACKOFF_MULTIPLIER ** (self._reconnect_attempt - 1)),
            _MAX_BACKOFF_SECONDS,
        )
        self._emit_status(
            STATUS_RECONNECTING,
            {FIELD_ATTEMPT: self._reconnect_attempt, FIELD_DELAY_SECONDS: delay},
        )
        await asyncio.sleep(delay)
        self._reconnect_elapsed_seconds += delay
        return True

    async def _listen(self, connection: Any) -> None:
        async for raw_frame in connection:
            self._dispatch(raw_frame)

    async def _send(self, payload: Dict[str, Any]) -> None:
        if self._connection is None:
            _LOGGER.warning("Dropping outbound message; not connected: %s", payload)
            return
        try:
            await self._connection.send(json.dumps(payload))
        except websockets.ConnectionClosed as exc:
            _LOGGER.warning("Failed to send %s; connection closed: %s", payload, exc)

    def _dispatch(self, raw_frame: Any) -> None:
        """Decode one wire frame and hand it to the registered callback."""
        try:
            message = json.loads(raw_frame)
        except (TypeError, json.JSONDecodeError) as exc:
            _LOGGER.warning("Discarding malformed frame: %s", exc)
            return

        if not isinstance(message, dict):
            _LOGGER.warning("Discarding non-object frame: %r", message)
            return

        self._publish(message)

    def _emit_status(self, status: str, extra_fields: Optional[Dict[str, Any]] = None) -> None:
        """Publish a synthetic connection-status update alongside real wire frames."""
        message: Dict[str, Any] = {FIELD_TYPE: MSG_TYPE_CONNECTION_STATUS, FIELD_STATUS: status}
        message.update(extra_fields or {})
        self._publish(message)

    def _publish(self, message: Dict[str, Any]) -> None:
        if self._on_message is None:
            return
        try:
            self._on_message(message)
        except Exception:
            _LOGGER.exception("on_message_callback raised while handling %r", message)

    def _handle_disconnect(self, exc: "websockets.ConnectionClosed") -> None:
        """Log a dropped connection and notify the GUI so it can show the
        reconnect overlay; the caller's `_reconnect_loop` takes it from here.
        """
        if self._closing:
            _LOGGER.info("[%s] connection closed during shutdown.", self._username)
            return
        _LOGGER.warning("[%s] connection to %s dropped: %s", self._username, self._server_url, exc)
        self._emit_status(STATUS_DISCONNECTED)
