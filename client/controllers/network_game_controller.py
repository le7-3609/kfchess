"""NetworkGameController — plays a match over a NetworkClient (Layer 6 / client).

Owns: the inbound frame queue and the whole wire vocabulary of a game in
progress — decoding `game_state`/`game_start`/`event_*`/disconnect frames into
the typed callbacks IGameController promises, and phrasing the notices a
networked match can produce (a dropped opponent, a failed reconnect, a rated
result).
Must not own: the socket itself (NetworkClient's), tkinter widgets, or game
rules. Nothing here may import server — the frame names below deliberately
restate the protocol rather than share a module with it.

NetworkClient delivers frames on its own background thread. They are parked in
a queue here and decoded only from `poll()`, which the window calls on the Tk
loop — so every listener callback reaches the UI on the UI thread, as
IGameController requires.
"""

import logging
import queue
from typing import Any, Callable, Dict, Optional

from shared.config import consts
from shared.io.moves_log import MoveLogEntry
from shared.model.position import Position
from client.controllers.game_controller import (
    GameControllerListener,
    GameNotice,
    GameSessionInfo,
    IGameController,
    NoticeLevel,
)
from client.network import protocol
from client.network.network_client import (
    FIELD_ATTEMPT,
    FIELD_DELAY_SECONDS,
    MSG_TYPE_CONNECTION_STATUS,
    NetworkClient,
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    STATUS_RECONNECTING,
    STATUS_RECONNECT_FAILED,
)
from client.network.network_snapshot_decoder import decode_game_snapshot
from client.notation.algebraic_notation import format_square, parse_square
from client.ui import consts as ui_consts

_LOGGER = logging.getLogger(__name__)

_VIEWER_COLOR = "viewer"

_PLACEHOLDER_OPPONENT_NAME = "Waiting for opponent..."
_DISCONNECTED_MESSAGE = "Connection lost. Attempting to reconnect…"
_RECONNECT_FAILED_MESSAGE = "Unable to reconnect to the server. The match cannot continue."
_FORFEIT_VICTORY_MESSAGE = "Your opponent forfeited by disconnecting — you win!"
_DEFAULT_OPPONENT_LABEL = "Your opponent"

# Maps this seat's "w"/"b" color onto the game_end frame's white/black rating
# keys — the frame speaks in seat names, the rest of the client in the terser
# color codes.
_RATING_KEY_BY_COLOR = {
    consts.COLOR_WHITE: protocol.FIELD_WHITE,
    consts.COLOR_BLACK: protocol.FIELD_BLACK,
}


class NetworkGameController(IGameController):
    """Turns the server's frame stream into one match, for a window to render."""

    def __init__(self, network_client: NetworkClient, username: str) -> None:
        self._network_client = network_client
        self._username = username

        self._inbox: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._listener: Optional[GameControllerListener] = None
        self._assigned_color: Optional[str] = None
        self._is_viewer = False
        self._room_id: Optional[str] = None
        # Remembered from the opening opponent_disconnected frame, because the
        # per-second countdown_tick frames carry no username of their own.
        self._disconnected_opponent_name: Optional[str] = None

        self._handlers: Dict[str, Callable[[Dict[str, Any]], None]] = {
            protocol.MSG_TYPE_GAME_STATE: self._on_game_state,
            protocol.MSG_TYPE_GAME_START: self._on_game_start,
            protocol.MSG_TYPE_ROOM_CREATED: self._on_room_created,
            protocol.MSG_TYPE_EVENT_PIECE_MOVED: self._on_piece_moved,
            protocol.MSG_TYPE_EVENT_SCORE_UPDATED: self._on_score_updated,
            protocol.MSG_TYPE_EVENT_PIECE_CAPTURED: self._on_piece_captured,
            protocol.MSG_TYPE_OPPONENT_DISCONNECTED: self._on_opponent_disconnected,
            protocol.MSG_TYPE_COUNTDOWN_TICK: self._on_countdown_tick,
            protocol.MSG_TYPE_OPPONENT_RECONNECTED: self._on_opponent_reconnected,
            protocol.MSG_TYPE_FORFEIT_VICTORY: self._on_forfeit_victory,
            protocol.MSG_TYPE_GAME_END: self._on_game_end,
            MSG_TYPE_CONNECTION_STATUS: self._on_connection_status,
            protocol.MSG_TYPE_ERROR: self._on_error,
        }

        # Connection-status → notice builder, declared once so a new status is a
        # new row rather than another elif. Each builder turns a status frame
        # into the GameNotice it should raise.
        self._status_notices: Dict[str, Callable[[Dict[str, Any]], GameNotice]] = {
            STATUS_DISCONNECTED: lambda message: GameNotice(
                NoticeLevel.TRANSIENT, _DISCONNECTED_MESSAGE
            ),
            STATUS_RECONNECTING: self._reconnecting_notice,
            STATUS_CONNECTED: lambda message: GameNotice.cleared(),
            STATUS_RECONNECT_FAILED: lambda message: GameNotice(
                NoticeLevel.TERMINAL, _RECONNECT_FAILED_MESSAGE
            ),
        }

    @property
    def poll_interval_ms(self) -> int:
        return ui_consts.NETWORK_POLL_MS

    @property
    def assigned_color(self) -> Optional[str]:
        return self._assigned_color

    @property
    def is_viewer(self) -> bool:
        return self._is_viewer

    def accept_frame(self, message: Dict[str, Any]) -> None:
        """Queue a frame that arrived before this controller was listening.

        The lobby opens the connection while still showing its own dialog, so
        the frames that decide the match — `game_start`, and whatever lands in
        the gap before `start()` redirects delivery — are received there and
        replayed here rather than dropped.
        """
        self._inbox.put(message)

    def start(self, listener: GameControllerListener) -> None:
        """Point the already-running client's frames at this controller's inbox.

        The connection is opened by the lobby, not here: calling
        `NetworkClient.start()` on a client that is already running raises, so
        this redirects the running one instead.
        """
        self._listener = listener
        self._network_client.set_message_callback(self._inbox.put)

    def poll(self) -> None:
        """Decode every frame queued since the last poll, on the caller's thread."""
        while True:
            try:
                message = self._inbox.get_nowait()
            except queue.Empty:
                return
            self._dispatch(message)

    def submit_move(self, source: Position, target: Position) -> None:
        self._network_client.send_move(format_square(source), format_square(target))

    def leave(self) -> None:
        self._network_client.stop()
        self._listener = None

    def _dispatch(self, message: Dict[str, Any]) -> None:
        handler = self._handlers.get(message.get(protocol.FIELD_TYPE))
        if handler is not None and self._listener is not None:
            handler(message)

    def _on_game_state(self, message: Dict[str, Any]) -> None:
        state = message.get(protocol.FIELD_STATE)
        if state is None:
            return
        self._listener.on_snapshot(decode_game_snapshot(state))

    def _on_game_start(self, message: Dict[str, Any]) -> None:
        self._assigned_color = message.get(protocol.FIELD_COLOR)
        self._is_viewer = self._assigned_color == _VIEWER_COLOR
        self._room_id = message.get(protocol.FIELD_ROOM_ID)
        self._listener.on_session_started(
            GameSessionInfo(
                assigned_color=None if self._is_viewer else self._assigned_color,
                opponent_name=message.get(protocol.FIELD_OPPONENT) or _PLACEHOLDER_OPPONENT_NAME,
                room_id=self._room_id,
                is_viewer=self._is_viewer,
            )
        )

    def _on_room_created(self, message: Dict[str, Any]) -> None:
        """A room exists but has no second player yet, so no seat is assigned.

        The window still needs a session to title itself with, and the player
        needs the room id to pass on — hence a placeholder opponent plus a
        notice that stands until `game_start` supersedes it.
        """
        self._room_id = message.get(protocol.FIELD_ROOM_ID)
        self._listener.on_session_started(
            GameSessionInfo(
                assigned_color=None,
                opponent_name=_PLACEHOLDER_OPPONENT_NAME,
                room_id=self._room_id,
            )
        )
        self._listener.on_notice(
            GameNotice(
                NoticeLevel.TRANSIENT,
                f"Room {self._room_id} created.\nWaiting for an opponent to join…",
            )
        )

    def _on_piece_moved(self, message: Dict[str, Any]) -> None:
        notation = (
            f"{message[protocol.FIELD_PIECE_TYPE]}{message[protocol.FIELD_FROM]}"
            f"{consts.NOTATION_MOVE_SEPARATOR}{message[protocol.FIELD_TO]}"
        )
        self._listener.on_move_recorded(
            MoveLogEntry(
                color=message[protocol.FIELD_COLOR],
                notation=notation,
                time_ms=message.get(protocol.FIELD_AT_MS, 0),
            )
        )

    def _on_score_updated(self, message: Dict[str, Any]) -> None:
        self._listener.on_score_changed(
            message.get(protocol.FIELD_WHITE_SCORE, consts.STARTING_SCORE),
            message.get(protocol.FIELD_BLACK_SCORE, consts.STARTING_SCORE),
        )

    def _on_piece_captured(self, message: Dict[str, Any]) -> None:
        square = message.get(protocol.FIELD_POS)
        if square:
            self._listener.on_capture(parse_square(square), message.get(protocol.FIELD_AT_MS, 0))

    def _on_opponent_disconnected(self, message: Dict[str, Any]) -> None:
        self._disconnected_opponent_name = (
            message.get(protocol.FIELD_USERNAME) or _DEFAULT_OPPONENT_LABEL
        )
        self._show_disconnect_countdown(message.get(protocol.FIELD_COUNTDOWN_SECONDS, 0))

    def _on_countdown_tick(self, message: Dict[str, Any]) -> None:
        self._show_disconnect_countdown(message.get(protocol.FIELD_SECONDS_REMAINING, 0))

    def _show_disconnect_countdown(self, seconds_remaining: int) -> None:
        name = self._disconnected_opponent_name or _DEFAULT_OPPONENT_LABEL
        self._listener.on_notice(
            GameNotice(
                NoticeLevel.TRANSIENT,
                f"{name} disconnected. Auto-resign in {seconds_remaining}s if they don't return…",
            )
        )

    def _on_opponent_reconnected(self, message: Dict[str, Any]) -> None:
        self._disconnected_opponent_name = None
        self._listener.on_notice(GameNotice.cleared())

    def _on_forfeit_victory(self, message: Dict[str, Any]) -> None:
        self._disconnected_opponent_name = None
        self._listener.on_notice(
            GameNotice(NoticeLevel.TERMINAL, _FORFEIT_VICTORY_MESSAGE, outcome=True)
        )

    def _on_game_end(self, message: Dict[str, Any]) -> None:
        """Report the final result, with this player's rating change if rated.

        A rated forfeit reaches this too: the frame carries a richer picture
        (reason, winner, and both ratings) than `forfeit_victory` alone, so it
        supersedes that notice rather than fighting it.
        """
        self._disconnected_opponent_name = None
        winner = message.get(protocol.FIELD_WINNER)
        text = self._describe_game_end(message.get(protocol.FIELD_REASON, ""), winner)
        outcome = None if winner is None else winner == self._assigned_color

        rating_key = _RATING_KEY_BY_COLOR.get(self._assigned_color)
        rating = message.get(rating_key) if rating_key else None
        if rating is not None:
            text += (
                f"\nNew rating: {rating[protocol.FIELD_NEW_ELO]}"
                f" ({rating[protocol.FIELD_ELO_CHANGE]:+d})"
            )

        self._listener.on_notice(GameNotice(NoticeLevel.TERMINAL, text, outcome))

    def _describe_game_end(self, reason: str, winner: Optional[str]) -> str:
        won = winner is not None and winner == self._assigned_color
        if reason == protocol.GAME_END_REASON_DISCONNECTION_TIMEOUT:
            return (
                "Your opponent forfeited by disconnecting — you win!"
                if won
                else "You forfeited by disconnecting."
            )
        if winner is None:
            return "Game over — draw."
        return "Game over — you win!" if won else "Game over — you lose."

    def _on_connection_status(self, message: Dict[str, Any]) -> None:
        """Turn NetworkClient's synthetic status frames into notices."""
        builder = self._status_notices.get(message.get(protocol.FIELD_STATUS))
        if builder is not None:
            self._listener.on_notice(builder(message))

    def _reconnecting_notice(self, message: Dict[str, Any]) -> GameNotice:
        attempt = message.get(FIELD_ATTEMPT)
        delay_seconds = message.get(FIELD_DELAY_SECONDS, 0.0)
        return GameNotice(
            NoticeLevel.TRANSIENT,
            f"Connection lost. Reconnecting… "
            f"(attempt {attempt}, retrying in {delay_seconds:.0f}s)",
        )

    def _on_error(self, message: Dict[str, Any]) -> None:
        _LOGGER.warning("Server error: %s", message.get(protocol.FIELD_MESSAGE))
