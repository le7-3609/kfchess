import logging
import os
import queue
import tkinter as tk
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple

from shared.config import consts
from shared.io.moves_log import MoveLogEntry
from shared.model.position import Position
from shared.view.game_snapshot import GameSnapshot
from client.algebraic_notation import format_square, parse_square
from client.network_client import (
    MSG_TYPE_CONNECTION_STATUS,
    NetworkClient,
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    STATUS_RECONNECTING,
    STATUS_RECONNECT_FAILED,
)
from client.network_snapshot_decoder import decode_game_snapshot
from client.ui import consts as ui_consts
from client.ui.preferences.board_themes import BOARD_THEMES, get_theme as get_board_theme
from client.ui.preferences.piece_themes import PIECE_THEMES, get_theme
from client.ui.preferences.user_settings_store import UserSettingsStore
from client.ui.rendering.info_panel import InfoPanel
from client.ui.rendering.pillow_renderer import PillowRenderer
from client.ui.window.image_view import TkImageView
from client.ui.window.reconnect_overlay import ReconnectOverlay

_LOGGER = logging.getLogger(__name__)

# Wire protocol vocabulary (mirrors server/application/dtos; client must not import server).
_MSG_TYPE_GAME_STATE = "game_state"
_MSG_TYPE_GAME_START = "game_start"
_MSG_TYPE_ERROR = "error"
_MSG_TYPE_OPPONENT_DISCONNECTED = "opponent_disconnected"
_MSG_TYPE_COUNTDOWN_TICK = "countdown_tick"
_MSG_TYPE_OPPONENT_RECONNECTED = "opponent_reconnected"
_MSG_TYPE_FORFEIT_VICTORY = "forfeit_victory"

_PLACEHOLDER_OPPONENT_NAME = "Waiting for opponent..."
_DISCONNECTED_MESSAGE = "Connection lost. Attempting to reconnect…"
_RECONNECT_FAILED_MESSAGE = "Unable to reconnect to the server. The match cannot continue."
_FORFEIT_VICTORY_MESSAGE = "Your opponent forfeited by disconnecting — you win!"
_DEFAULT_OPPONENT_LABEL = "Your opponent"


@dataclass(frozen=True)
class _CaptureFlash:
    """A fading marker on the square a capture happened, expiring on the game clock."""

    pos: Position
    started_ms: int

    def alpha_at(self, clock_ms: int) -> int:
        elapsed = clock_ms - self.started_ms
        if elapsed < 0 or elapsed >= ui_consts.CAPTURE_FLASH_MS:
            return 0
        return round(
            ui_consts.CAPTURE_FLASH_MAX_ALPHA * (1 - elapsed / ui_consts.CAPTURE_FLASH_MS)
        )


class NetworkedGameWindow:
    """Owns the tkinter Tk root, canvas, click bindings, and network poll loop."""

    def __init__(
        self,
        network_client: NetworkClient,
        renderer: PillowRenderer,
        username: str,
        title: str = ui_consts.WINDOW_TITLE,
        board_size: int = ui_consts.BOARD_SIZE,
        assets_dir: Optional[str] = None,
        settings_store: Optional[UserSettingsStore] = None,
    ) -> None:
        self.network_client = network_client
        self.renderer = renderer
        self.username = username
        self.board_size = board_size
        self.assets_dir = assets_dir
        self.settings_store = settings_store or UserSettingsStore()

        self.info_panel = InfoPanel(username, _PLACEHOLDER_OPPONENT_NAME)
        self._message_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._latest_snapshot: Optional[GameSnapshot] = None
        self._pending_source: Optional[Tuple[int, int]] = None
        self._assigned_color: Optional[str] = None
        self.is_viewer: bool = False
        self.room_id: Optional[str] = None

        self._moves: List[MoveLogEntry] = []
        self._scores: Dict[str, int] = {
            consts.COLOR_WHITE: consts.STARTING_SCORE,
            consts.COLOR_BLACK: consts.STARTING_SCORE,
        }
        self._capture_flashes: List[_CaptureFlash] = []
        # Remembered from the opening opponent_disconnected frame so the
        # per-second countdown_tick frames — which carry no username of their
        # own — can keep showing who the countdown is about.
        self._disconnected_opponent_name: Optional[str] = None

        self.root = tk.Tk()
        self.root.title(title)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._reconnect_overlay = ReconnectOverlay(self.root)

        self._build_settings_vars()
        self._build_menu()
        self._build_canvas(board_size)

    def _build_settings_vars(self) -> None:
        """Load saved user settings into the tk variables the menu binds to."""
        self._settings = self.settings_store.load()
        self._piece_theme_var = tk.StringVar(master=self.root, value=self._settings.piece_theme)
        self._board_theme_var = tk.StringVar(master=self.root, value=self._settings.board_theme)

    def _build_menu(self) -> None:
        """Build the Settings menu."""
        menu_bar = tk.Menu(self.root)
        settings_menu = tk.Menu(menu_bar, tearoff=0)
        self._add_radio_submenu(
            settings_menu, "Piece Theme",
            [(theme.display_name, theme.theme_id) for theme in PIECE_THEMES],
            self._piece_theme_var, self._on_piece_theme_selected,
        )
        self._add_radio_submenu(
            settings_menu, "Board Theme",
            [(theme.display_name, theme.theme_id) for theme in BOARD_THEMES],
            self._board_theme_var, self._on_board_theme_selected,
        )
        menu_bar.add_cascade(label="Settings", menu=settings_menu)
        self.root.config(menu=menu_bar)

    def _add_radio_submenu(self, parent: tk.Menu, label: str, options, variable, on_select) -> None:
        submenu = tk.Menu(parent, tearoff=0)
        for option_label, value in options:
            submenu.add_radiobutton(
                label=option_label,
                value=value,
                variable=variable,
                command=lambda selected=value: on_select(selected),
            )
        parent.add_cascade(label=label, menu=submenu)

    def _on_piece_theme_selected(self, theme_id: str) -> None:
        if self.assets_dir is None:
            return
        theme = get_theme(theme_id)
        self.renderer.reload_sprites(os.path.join(self.assets_dir, theme.folder_name))
        self._save_settings(replace(self._settings, piece_theme=theme_id))
        self._refresh()

    def _on_board_theme_selected(self, theme_id: str) -> None:
        theme = get_board_theme(theme_id)
        self.renderer.set_board_theme(theme.light_color, theme.dark_color)
        self._save_settings(replace(self._settings, board_theme=theme_id))
        self._refresh()

    def _save_settings(self, settings) -> None:
        self._settings = settings
        self.settings_store.save(settings)

    def _build_canvas(self, board_size: int) -> None:
        self.canvas_width = ui_consts.SIDE_PANEL_WIDTH * 2 + board_size
        self.canvas_height = ui_consts.PANEL_TOP_HEIGHT + board_size
        self.canvas = tk.Canvas(
            self.root, width=self.canvas_width, height=self.canvas_height, highlightthickness=0
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        canvas_image_id = self.canvas.create_image(0, 0, anchor="nw")
        self.view = TkImageView(self.canvas, canvas_image_id)
        self.renderer.resize(board_size, board_size)

        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Button-1>", self._on_left_click)

    def run(self) -> None:
        """Open the connection, start the queue-poll loop, and block on the Tk main loop."""
        self.network_client.start(on_message_callback=self._message_queue.put)
        self._schedule_queue_poll()
        self.root.mainloop()

    def attach_and_run(self) -> None:
        """Take over an already-started NetworkClient, then block on the Tk main loop.

        Used when the caller (LobbyWindow) opened the persistent connection
        before this window existed, so it had somewhere to receive
        `game_start` while still showing its own dialog. Calling
        `network_client.start()` again here would raise, so this redirects
        the running client's callback instead of opening a fresh one.
        """
        self.network_client.set_message_callback(self._message_queue.put)
        self._schedule_queue_poll()
        self.root.mainloop()

    def _schedule_queue_poll(self) -> None:
        self.root.after(ui_consts.NETWORK_POLL_MS, self._process_network_queue)

    def _process_network_queue(self) -> None:
        """Drain every frame NetworkClient has queued since the last poll, then reschedule."""
        while True:
            try:
                message = self._message_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_message(message)
        self._schedule_queue_poll()

    def _handle_message(self, message: Dict[str, Any]) -> None:
        msg_type = message.get("type")
        if msg_type == _MSG_TYPE_GAME_STATE:
            self._on_game_state(message)
        elif msg_type == _MSG_TYPE_GAME_START:
            self._on_game_start(message)
        elif msg_type == "event_piece_moved":
            self._on_event_piece_moved(message)
        elif msg_type == "event_score_updated":
            self._on_event_score_updated(message)
        elif msg_type == "event_piece_captured":
            self._on_event_piece_captured(message)
        elif msg_type == _MSG_TYPE_OPPONENT_DISCONNECTED:
            self._on_opponent_disconnected(message)
        elif msg_type == _MSG_TYPE_COUNTDOWN_TICK:
            self._on_countdown_tick(message)
        elif msg_type == _MSG_TYPE_OPPONENT_RECONNECTED:
            self._on_opponent_reconnected(message)
        elif msg_type == _MSG_TYPE_FORFEIT_VICTORY:
            self._on_forfeit_victory(message)
        elif msg_type == _MSG_TYPE_ERROR:
            _LOGGER.warning("Server error: %s", message.get("message"))
        elif msg_type == MSG_TYPE_CONNECTION_STATUS:
            self._on_connection_status(message)

    def _on_event_piece_moved(self, message: Dict[str, Any]) -> None:
        color = message["color"]
        piece_type = message["piece_type"]
        from_sq = message["from"]
        to_sq = message["to"]
        at_ms = message.get("at_ms", 0)
        notation = f"{piece_type}{from_sq}{consts.NOTATION_MOVE_SEPARATOR}{to_sq}"
        self._moves.append(MoveLogEntry(color=color, notation=notation, time_ms=at_ms))
        self._refresh()

    def _on_event_score_updated(self, message: Dict[str, Any]) -> None:
        self._scores = {
            consts.COLOR_WHITE: message.get("white_score", consts.STARTING_SCORE),
            consts.COLOR_BLACK: message.get("black_score", consts.STARTING_SCORE),
        }
        self._refresh()

    def _on_event_piece_captured(self, message: Dict[str, Any]) -> None:
        pos_sq = message.get("pos")
        if pos_sq:
            self._capture_flashes.append(
                _CaptureFlash(pos=parse_square(pos_sq), started_ms=message.get("at_ms", 0))
            )

    def _on_opponent_disconnected(self, message: Dict[str, Any]) -> None:
        """Show the auto-resign countdown the server started for the dropped opponent."""
        self._disconnected_opponent_name = message.get("username") or _DEFAULT_OPPONENT_LABEL
        self._show_disconnect_countdown(message.get("countdown_seconds", 0))

    def _on_countdown_tick(self, message: Dict[str, Any]) -> None:
        """Advance the live countdown; `countdown_tick` carries no username of its own."""
        self._show_disconnect_countdown(message.get("seconds_remaining", 0))

    def _show_disconnect_countdown(self, seconds_remaining: int) -> None:
        name = self._disconnected_opponent_name or _DEFAULT_OPPONENT_LABEL
        self._reconnect_overlay.show(
            f"{name} disconnected. Auto-resign in {seconds_remaining}s if they don't return…"
        )

    def _on_opponent_reconnected(self, message: Dict[str, Any]) -> None:
        self._disconnected_opponent_name = None
        self._reconnect_overlay.hide()

    def _on_forfeit_victory(self, message: Dict[str, Any]) -> None:
        self._disconnected_opponent_name = None
        self._reconnect_overlay.show_terminal(_FORFEIT_VICTORY_MESSAGE, on_close=self._on_close)

    def _on_connection_status(self, message: Dict[str, Any]) -> None:
        """Drive the modal reconnect overlay from NetworkClient's status frames."""
        status = message.get("status")
        if status == STATUS_DISCONNECTED:
            self._reconnect_overlay.show(_DISCONNECTED_MESSAGE)
        elif status == STATUS_RECONNECTING:
            attempt = message.get("attempt")
            delay_seconds = message.get("delay_seconds", 0.0)
            self._reconnect_overlay.show(
                f"Connection lost. Reconnecting… (attempt {attempt}, retrying in {delay_seconds:.0f}s)"
            )
        elif status == STATUS_CONNECTED:
            self._reconnect_overlay.hide()
        elif status == STATUS_RECONNECT_FAILED:
            self._reconnect_overlay.show_terminal(_RECONNECT_FAILED_MESSAGE, on_close=self._on_close)

    def _on_game_state(self, message: Dict[str, Any]) -> None:
        state = message.get("state")
        if state is None:
            return
        self._latest_snapshot = decode_game_snapshot(state)
        self._refresh()

    def _on_game_start(self, message: Dict[str, Any]) -> None:
        color = message.get("color")
        opponent = message.get("opponent") or _PLACEHOLDER_OPPONENT_NAME
        room_id = message.get("room_id")
        self.room_id = room_id
        self._assigned_color = color
        self.is_viewer = (color == "viewer")

        if color == consts.COLOR_BLACK:
            self.info_panel.white_name = opponent
            self.info_panel.black_name = self.username
            self.renderer.set_flipped(True)
        elif color == "viewer":
            self.info_panel.white_name = "White"
            self.info_panel.black_name = "Black"
        else:
            self.info_panel.white_name = self.username
            self.info_panel.black_name = opponent

        color_label = "Spectator" if self.is_viewer else ui_consts.COLOR_DISPLAY_NAMES.get(color, color)
        room_suffix = f" — Room: {room_id}" if room_id else ""
        self.root.title(f"{ui_consts.WINDOW_TITLE} — {self.username} ({color_label}){room_suffix}")
        self._refresh()

    def _canvas_to_cell(self, event_x: int, event_y: int) -> Optional[Tuple[int, int]]:
        panel_width = ui_consts.SIDE_PANEL_WIDTH
        top_height = ui_consts.PANEL_TOP_HEIGHT
        board_x_offset = panel_width + (self.canvas_width - panel_width * 2 - self.board_size) // 2
        board_y_offset = top_height + (self.canvas_height - top_height - self.board_size) // 2

        board_x = event_x - board_x_offset
        board_y = event_y - board_y_offset
        return self.renderer.get_geometry().pixel_to_cell(board_x, board_y)

    def _on_left_click(self, event) -> None:
        """Intercept the two-click select/move gesture and send a complete move."""
        if self.is_viewer or self._latest_snapshot is None:
            return
        cell = self._canvas_to_cell(event.x, event.y)
        if cell is None:
            return

        if self._pending_source is None:
            self._pending_source = cell
            self._refresh()
            return

        source, self._pending_source = self._pending_source, None
        if source != cell:
            from_square = format_square(Position(*source))
            to_square = format_square(Position(*cell))
            self.network_client.send_move(from_square, to_square)
        self._refresh()

    def _on_resize(self, event) -> None:
        if event.widget != self.canvas:
            return
        self.canvas_width = event.width
        self.canvas_height = event.height

        minimum = ui_consts.MIN_BOARD_DIMENSION_PX
        available_board_w = max(minimum, self.canvas_width - ui_consts.SIDE_PANEL_WIDTH * 2)
        available_board_h = max(minimum, self.canvas_height - ui_consts.PANEL_TOP_HEIGHT)
        self.board_size = min(available_board_w, available_board_h)

        self.renderer.resize(self.board_size, self.board_size)
        self._refresh()

    def _refresh(self) -> None:
        if self._latest_snapshot is None:
            return
        snapshot = self._latest_snapshot
        if self._pending_source is not None:
            snapshot = replace(snapshot, selected_pos=Position(*self._pending_source))

        self.renderer.draw(snapshot)
        board_img = self.renderer.get_image()
        self._draw_capture_flashes(board_img, snapshot.clock_ms)
        composed = self.info_panel.render(
            board_img,
            self.board_size,
            self.canvas_width,
            self.canvas_height,
            moves=self._moves,
            white_score=self._scores[consts.COLOR_WHITE],
            black_score=self._scores[consts.COLOR_BLACK],
        )
        self.view.show(composed)

    def _draw_capture_flashes(self, board_img, clock_ms: int) -> None:
        self._capture_flashes = [
            flash for flash in self._capture_flashes if flash.alpha_at(clock_ms) > 0
        ]
        geometry = self.renderer.get_geometry()
        for flash in self._capture_flashes:
            rect = geometry.cell_to_pixel(flash.pos.row, flash.pos.col)
            board_img.fill_rect(
                rect.x, rect.y, rect.width, rect.height,
                (*ui_consts.CAPTURE_FLASH_COLOR, flash.alpha_at(clock_ms)),
            )

    def _on_close(self) -> None:
        self.network_client.stop()
        self.root.destroy()

