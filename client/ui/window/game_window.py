"""Game window — the tkinter shell every match is played in (Layer 6 / client UI).

Owns: the Tk root, canvas and menus, the click gesture that turns two clicks
into one move, the poll loop that drives the match, and the little view state
(capture flashes, move list, scores, notices) the render pass paints through
Img.
Must not own: sockets, wire frames, game rules, or the simulation clock. It
holds an IGameController and knows nothing about which kind it got — an
online seat and an offline match reach this class through the same seam, so
nothing below differs between the two modes except the capability flags it
asks the controller about.

As a GameControllerListener it only records what it is told; every callback
may land part-way through a simulation tick, and tkinter must be touched from
its own loop, so the next `_refresh()` is what actually draws.
"""

import logging
import os
import tkinter as tk
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from shared.config import consts
from shared.io.moves_log import MoveLogEntry
from shared.model.position import Position
from shared.view.game_snapshot import GameSnapshot
from client.controllers.game_controller import (
    GameControllerListener,
    GameNotice,
    GameSessionInfo,
    IGameController,
    NoticeLevel,
)
from client.ui import consts as ui_consts
from client.ui.preferences.board_themes import BOARD_THEMES, get_theme as get_board_theme
from client.ui.preferences.piece_themes import PIECE_THEMES, get_theme
from client.ui.preferences.user_settings_store import UserSettings, UserSettingsStore
from client.ui.rendering.img import Img
from client.ui.rendering.info_panel import InfoPanel
from client.ui.rendering.pillow_renderer import PillowRenderer
from client.ui.sound_player import SoundPlayer
from client.ui.window.history_dialog import prompt_and_save, show_load_history_dialog
from client.ui.window.image_view import TkImageView
from client.ui.window.reconnect_overlay import ReconnectOverlay

_LOGGER = logging.getLogger(__name__)

_SPECTATOR_LABEL = "Spectator"


@dataclass(frozen=True)
class _CaptureFlash:
    """A fading marker on the square a capture happened, expiring on the game clock."""

    pos: Position
    started_ms: int

    def alpha_at(self, clock_ms: int) -> int:
        """Flash opacity at *clock_ms*, fading linearly to nothing over its lifetime."""
        elapsed = clock_ms - self.started_ms
        if elapsed < 0 or elapsed >= ui_consts.CAPTURE_FLASH_MS:
            return 0
        return round(
            ui_consts.CAPTURE_FLASH_MAX_ALPHA * (1 - elapsed / ui_consts.CAPTURE_FLASH_MS)
        )


class GameWindow(GameControllerListener):
    """Renders one match and feeds the player's clicks back to its controller."""

    def __init__(
        self,
        controller: IGameController,
        renderer: PillowRenderer,
        username: str,
        title: str = ui_consts.WINDOW_TITLE,
        board_size: int = ui_consts.BOARD_SIZE,
        assets_dir: Optional[str] = None,
        settings_store: Optional[UserSettingsStore] = None,
    ) -> None:
        self.controller = controller
        self.renderer = renderer
        self.username = username
        self.board_size = board_size
        self.assets_dir = assets_dir
        self.settings_store = settings_store or UserSettingsStore()

        self.info_panel = InfoPanel(ui_consts.DEFAULT_WHITE_NAME, ui_consts.DEFAULT_BLACK_NAME)
        self._latest_snapshot: Optional[GameSnapshot] = None
        self._pending_source: Optional[Tuple[int, int]] = None
        self._moves: List[MoveLogEntry] = []
        self._scores: Dict[str, int] = self._blank_scores()
        self._capture_flashes: List[_CaptureFlash] = []
        self._sound_player = SoundPlayer(assets_dir)
        self._poll_id: Optional[str] = None

        self.root = tk.Tk()
        self.root.title(title)
        self.root.protocol(ui_consts.WM_DELETE_WINDOW_PROTOCOL, self.close)

        self._overlay = ReconnectOverlay(self.root)

        self._build_settings_vars()
        self._build_menu()
        self._build_canvas(board_size)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the match, begin polling it, and block on the Tk main loop."""
        self.controller.start(self)
        self._schedule_poll()
        self.root.mainloop()

    def close(self) -> None:
        if self._poll_id is not None:
            self.root.after_cancel(self._poll_id)
        self.controller.leave()
        self.root.destroy()

    def _schedule_poll(self) -> None:
        self._poll_id = self.root.after(self.controller.poll_interval_ms, self._poll)

    def _poll(self) -> None:
        """Let the controller deliver whatever the match produced, then reschedule."""
        self.controller.poll()
        self._schedule_poll()

    # ------------------------------------------------------------------
    # GameControllerListener — record only; _refresh() paints
    # ------------------------------------------------------------------

    def on_session_started(self, session: GameSessionInfo) -> None:
        self._apply_seat(session)
        self._apply_title(session)
        self._refresh()

    def on_snapshot(self, snapshot: GameSnapshot) -> None:
        self._latest_snapshot = snapshot
        self._refresh()

    def on_move_recorded(self, entry: MoveLogEntry) -> None:
        self._moves.append(entry)

    def on_score_changed(self, white_score: int, black_score: int) -> None:
        self._scores = {consts.COLOR_WHITE: white_score, consts.COLOR_BLACK: black_score}

    def on_capture(self, pos: Position, at_ms: int) -> None:
        self._capture_flashes.append(_CaptureFlash(pos=pos, started_ms=at_ms))

    def on_notice(self, notice: GameNotice) -> None:
        if notice.level is NoticeLevel.CLEARED:
            self._overlay.hide()
        elif notice.level is NoticeLevel.TERMINAL:
            self._play_ending_sound(notice.outcome)
            self._overlay.show_terminal(notice.text, on_close=self.close)
        else:
            self._overlay.show(notice.text)

    def _play_ending_sound(self, outcome: Optional[bool]) -> None:
        if outcome is True:
            self._sound_player.play_win()
        elif outcome is False:
            self._sound_player.play_lose()

    @staticmethod
    def _blank_scores() -> Dict[str, int]:
        return {color: consts.STARTING_SCORE for color in consts.ALL_COLORS}

    def _apply_seat(self, session: GameSessionInfo) -> None:
        """Name the two panels and flip the board for a player seated as Black.

        A seat of None covers both a spectator and an offline match nobody
        "owns", so the board stays in White's orientation and the panels fall
        back to the color names.
        """
        if session.assigned_color == consts.COLOR_BLACK:
            self.info_panel.white_name = session.opponent_name
            self.info_panel.black_name = self.username
            self.renderer.set_flipped(True)
        elif session.assigned_color == consts.COLOR_WHITE:
            self.info_panel.white_name = self.username
            self.info_panel.black_name = session.opponent_name
        else:
            self.info_panel.white_name = ui_consts.DEFAULT_WHITE_NAME
            self.info_panel.black_name = ui_consts.DEFAULT_BLACK_NAME

    def _apply_title(self, session: GameSessionInfo) -> None:
        if session.is_viewer:
            role = _SPECTATOR_LABEL
        else:
            role = ui_consts.COLOR_DISPLAY_NAMES.get(session.assigned_color, "")
        role_suffix = f" ({role})" if role else ""
        room_suffix = f" — Room: {session.room_id}" if session.room_id else ""
        self.root.title(f"{ui_consts.WINDOW_TITLE} — {self.username}{role_suffix}{room_suffix}")

    # ------------------------------------------------------------------
    # Widgets
    # ------------------------------------------------------------------

    def _build_settings_vars(self) -> None:
        """Load saved user settings into the tk variables the menus bind to."""
        self._settings = self.settings_store.load()
        self._piece_theme_var = tk.StringVar(master=self.root, value=self._settings.piece_theme)
        self._board_theme_var = tk.StringVar(master=self.root, value=self._settings.board_theme)
        self._speed_var = tk.IntVar(master=self.root, value=self._settings.speed_level_ms)
        self._cooldown_var = tk.IntVar(master=self.root, value=self._settings.cooldown_level_ms)

    def _build_menu(self) -> None:
        """Build the menu bar, offering only what this controller can honour."""
        menu_bar = tk.Menu(self.root)
        if self.controller.history is not None:
            menu_bar.add_cascade(label=ui_consts.MENU_LABEL_GAME, menu=self._build_game_menu(menu_bar))
        menu_bar.add_cascade(
            label=ui_consts.MENU_LABEL_SETTINGS, menu=self._build_settings_menu(menu_bar)
        )
        self.root.config(menu=menu_bar)

    def _build_game_menu(self, menu_bar: tk.Menu) -> tk.Menu:
        game_menu = tk.Menu(menu_bar, tearoff=0)
        game_menu.add_command(label="Save History...", command=self._save_history)
        game_menu.add_command(label="Load History...", command=self._load_history)
        return game_menu

    def _build_settings_menu(self, menu_bar: tk.Menu) -> tk.Menu:
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
        # Speed and cooldown belong to the running simulation, so they are
        # offered only where this window owns one — a networked seat cannot
        # change what the server decides.
        if self.controller.supports_preferences:
            self._add_radio_submenu(
                settings_menu, "Movement Speed",
                list(ui_consts.SPEED_PRESETS_MS.items()), self._speed_var, self._on_speed_selected,
            )
            self._add_radio_submenu(
                settings_menu, "Cooldown Time",
                list(ui_consts.COOLDOWN_PRESETS_MS.items()), self._cooldown_var,
                self._on_cooldown_selected,
            )
        return settings_menu

    def _add_radio_submenu(self, parent: tk.Menu, label: str, options, variable, on_select) -> None:
        """Attach a submenu of radio options to *parent*.

        Each entry of *options* is a (display label, value) pair; selecting one
        sets *variable* and calls *on_select* with that value.
        """
        submenu = tk.Menu(parent, tearoff=0)
        for option_label, value in options:
            submenu.add_radiobutton(
                label=option_label,
                value=value,
                variable=variable,
                # Bound as a default so each entry keeps its own value rather
                # than closing over the loop variable.
                command=lambda selected=value: on_select(selected),
            )
        parent.add_cascade(label=label, menu=submenu)

    def _build_canvas(self, board_size: int) -> None:
        """Create the canvas, its image view, and the click/resize bindings."""
        self.canvas_width = ui_consts.SIDE_PANEL_WIDTH * ui_consts.SIDE_PANEL_COUNT + board_size
        self.canvas_height = ui_consts.PANEL_TOP_HEIGHT + board_size
        self.canvas = tk.Canvas(
            self.root, width=self.canvas_width, height=self.canvas_height, highlightthickness=0
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        canvas_image_id = self.canvas.create_image(0, 0, anchor=ui_consts.ANCHOR_NORTH_WEST)
        self.view = TkImageView(self.canvas, canvas_image_id)
        self.renderer.resize(board_size, board_size)

        self.canvas.bind(ui_consts.EVENT_CONFIGURE, self._on_resize)
        self.canvas.bind(ui_consts.EVENT_LEFT_CLICK, self._on_left_click)
        if self.controller.supports_jump:
            self.canvas.bind(ui_consts.EVENT_RIGHT_CLICK, self._on_right_click)

    # ------------------------------------------------------------------
    # Settings actions
    # ------------------------------------------------------------------

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

    def _on_speed_selected(self, ms_per_square: int) -> None:
        self._save_settings(replace(self._settings, speed_level_ms=ms_per_square))
        self._apply_preferences()

    def _on_cooldown_selected(self, cooldown_ms: int) -> None:
        self._save_settings(replace(self._settings, cooldown_level_ms=cooldown_ms))
        self._apply_preferences()

    def _apply_preferences(self) -> None:
        self.controller.apply_preferences(
            self._settings.speed_level_ms, self._settings.cooldown_level_ms
        )

    def _save_settings(self, settings: UserSettings) -> None:
        self._settings = settings
        self.settings_store.save(settings)

    def _save_history(self) -> None:
        prompt_and_save(
            self.root,
            self.controller.history,
            self.info_panel.white_name,
            self.info_panel.black_name,
            None,
        )

    def _load_history(self) -> None:
        show_load_history_dialog(self.root, self.controller.history, self._build_replay_renderer)

    def _build_replay_renderer(self) -> PillowRenderer:
        """A renderer for a replay window, themed like the live board but
        independent of it — the two size their boards separately."""
        theme = get_theme(self._settings.piece_theme)
        sprite_path = os.path.join(self.assets_dir, theme.folder_name) if self.assets_dir else ""
        renderer = PillowRenderer(sprite_path)
        board_theme = get_board_theme(self._settings.board_theme)
        renderer.set_board_theme(board_theme.light_color, board_theme.dark_color)
        return renderer

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def _canvas_to_cell(self, event_x: int, event_y: int) -> Optional[Tuple[int, int]]:
        panel_width = ui_consts.SIDE_PANEL_WIDTH
        top_height = ui_consts.PANEL_TOP_HEIGHT
        board_x_offset = panel_width + (
            self.canvas_width - panel_width * ui_consts.SIDE_PANEL_COUNT - self.board_size
        ) // ui_consts.CENTERING_DIVISOR
        board_y_offset = top_height + (
            self.canvas_height - top_height - self.board_size
        ) // ui_consts.CENTERING_DIVISOR

        board_x = event_x - board_x_offset
        board_y = event_y - board_y_offset
        return self.renderer.get_geometry().pixel_to_cell(board_x, board_y)

    def _on_left_click(self, event) -> None:
        """Drive the two-click gesture: the first picks a source, the second moves.

        The pending source is held here rather than in the simulation, because
        a networked seat has no simulation to hold it in — the controller is
        told about the selection so a local one can still answer with legal
        moves, but the highlight is this window's either way.
        """
        if self.controller.is_viewer or self._latest_snapshot is None:
            return
        cell = self._canvas_to_cell(event.x, event.y)
        if cell is None:
            return

        pos = Position(*cell)
        target_piece = self._latest_snapshot.pieces.get(pos)
        assigned_color = self.controller.assigned_color
        if not isinstance(assigned_color, str):
            assigned_color = None

        if self._pending_source is None:
            if assigned_color is not None and (target_piece is None or target_piece.color != assigned_color):
                return
            self._pending_source = cell
            self.controller.submit_select(pos)
            self._refresh()
            return

        source_cell = self._pending_source
        source_pos = Position(*source_cell)
        source_piece = self._latest_snapshot.pieces.get(source_pos)

        if cell == source_cell:
            self._pending_source = None
            self.controller.submit_select(pos)
            self._refresh()
            return

        if target_piece is not None and (
            (assigned_color is not None and target_piece.color == assigned_color)
            or (assigned_color is None and source_piece is not None and target_piece.color == source_piece.color)
        ):
            if pos in self._latest_snapshot.castle_targets:
                self._pending_source = None
                self.controller.submit_move(source_pos, pos)
            else:
                self._pending_source = cell
                self.controller.submit_select(pos)
            self._refresh()
            return

        self._pending_source = None
        self.controller.submit_move(source_pos, pos)
        self._refresh()

    def _on_right_click(self, event) -> None:
        """Jump the piece under the cursor in place, regardless of any selection."""
        if self.controller.is_viewer or self._latest_snapshot is None:
            return
        cell = self._canvas_to_cell(event.x, event.y)
        if cell is None:
            return
        pos = Position(*cell)
        assigned_color = self.controller.assigned_color
        if not isinstance(assigned_color, str):
            assigned_color = None
        if assigned_color is not None:
            piece_snap = self._latest_snapshot.pieces.get(pos)
            if piece_snap is None or piece_snap.color != assigned_color:
                return
        self.controller.submit_jump(pos)
        self._refresh()

    def _on_resize(self, event) -> None:
        if event.widget != self.canvas:
            return
        self.canvas_width = event.width
        self.canvas_height = event.height

        minimum = ui_consts.MIN_BOARD_DIMENSION_PX
        available_board_w = max(
            minimum, self.canvas_width - ui_consts.SIDE_PANEL_WIDTH * ui_consts.SIDE_PANEL_COUNT
        )
        available_board_h = max(minimum, self.canvas_height - ui_consts.PANEL_TOP_HEIGHT)
        self.board_size = min(available_board_w, available_board_h)

        self.renderer.resize(self.board_size, self.board_size)
        self._refresh()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

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

    def _draw_capture_flashes(self, board_img: Img, clock_ms: int) -> None:
        """Paint and age the capture markers collected by on_capture.

        Drawn straight onto the renderer's finished board Img: PillowRenderer
        composes a fresh image every frame, so overlaying here marks only this
        frame and leaves the renderer free of any notion of events.
        """
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
