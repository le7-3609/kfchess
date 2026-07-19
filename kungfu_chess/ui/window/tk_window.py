"""Tkinter window — the UI window/controls layer.

Owns: the tkinter Tk root, canvas, menus/dialogs, translating mouse clicks
into GameService commands, and driving the render loop (advance clock ->
get snapshot -> draw -> show) once per tick. Every pixel shown came from an
Img composed by PillowRenderer; tkinter here only displays the already-
finished frame.

Talks to the application only through GameService: it submits commands
(click / right_click / advance_clock), reads queries (get_snapshot /
get_moves / history), and subscribes to domain events. It never touches the
GameEngine, the board/state repositories, or the arbiter directly — that is
the whole point of the UI/application split. Selection state lives inside the
engine and arrives back through the snapshot; this layer keeps none of it.

As an Observer it learns about captures, scores and game-over from published
events rather than by polling the snapshot for them. Everything those events
lead to is still painted through Img, in the render pass — see on_event.
"""

import os
import time
import tkinter as tk
from dataclasses import dataclass, replace
from typing import Callable, Dict, Optional, Type

from kungfu_chess.config import consts
from kungfu_chess.events import (
    Event,
    GameEndedEvent,
    GameStartedEvent,
    Observer,
    PieceCapturedEvent,
    ScoreUpdatedEvent,
)
from kungfu_chess.model.position import Position
from kungfu_chess.service import GameService
from kungfu_chess.ui.preferences.board_themes import BOARD_THEMES, get_theme as get_board_theme
from kungfu_chess.ui.preferences.piece_themes import PIECE_THEMES, get_theme
from kungfu_chess.ui.preferences.user_settings_store import UserSettings, UserSettingsStore
from kungfu_chess.ui.rendering.img import Img
from kungfu_chess.ui.rendering.info_panel import InfoPanel
from kungfu_chess.ui.rendering.pillow_renderer import PillowRenderer
from kungfu_chess.ui.window.history_dialog import prompt_and_save, show_load_history_dialog
from kungfu_chess.ui.window.image_view import TkImageView


@dataclass(frozen=True)
class _CaptureFlash:
    """A fading marker on the square a capture happened, expiring on the game clock."""

    pos: Position
    started_ms: int

    def alpha_at(self, clock_ms: int) -> int:
        """Flash opacity at *clock_ms*, fading linearly to nothing over its lifetime."""
        elapsed = clock_ms - self.started_ms
        if elapsed < 0 or elapsed >= consts.CAPTURE_FLASH_MS:
            return 0
        return round(
            consts.CAPTURE_FLASH_MAX_ALPHA * (1 - elapsed / consts.CAPTURE_FLASH_MS)
        )


class TkGameWindow(Observer):
    """Owns the tkinter Tk root, canvas, click bindings, and tick loop."""

    def __init__(
        self,
        service: GameService,
        renderer: PillowRenderer,
        title: str = consts.WINDOW_TITLE,
        board_size: int = consts.BOARD_SIZE,
        white_name: str = consts.DEFAULT_WHITE_NAME,
        black_name: str = consts.DEFAULT_BLACK_NAME,
        assets_dir: Optional[str] = None,
        settings_store: Optional[UserSettingsStore] = None,
    ):
        self.service = service
        self.renderer = renderer
        self.board_size = board_size
        self.white_name = white_name
        self.black_name = black_name
        self.info_panel = InfoPanel(self.white_name, self.black_name)
        self.assets_dir = assets_dir
        self.settings_store = settings_store or UserSettingsStore()

        self._capture_flashes: list[_CaptureFlash] = []
        self._scores: Dict[str, int] = self._blank_scores()
        self._pending_game_over: Optional[GameEndedEvent] = None

        self.root = tk.Tk()
        self.root.title(title)

        self._build_settings_vars()
        self._build_menu()
        self._build_canvas(board_size)
        self._subscribe_to_game_events()
        self._start_tick_loop()

    def _subscribe_to_game_events(self) -> None:
        """Attach this window to the simulation's event stream.

        Only the events this window actually reacts to are named, so a running
        game does not wake the UI for every move and every started motion —
        those are already covered by the per-frame snapshot.
        """
        self._event_handlers: Dict[Type[Event], Callable[[Event], None]] = {
            PieceCapturedEvent: self._on_piece_captured,
            ScoreUpdatedEvent: self._on_score_updated,
            GameEndedEvent: self._on_game_ended,
            GameStartedEvent: self._on_game_started,
        }
        self.service.subscribe(self, *self._event_handlers)

    def on_event(self, event: Event) -> None:
        """Record what the simulation announced; the render pass paints it.

        Deliberately draws nothing. This is called from inside a GameService
        command — part-way through a simulation tick — and tkinter must only be
        touched from its own loop, so anything drawn here would both race the
        toolkit and repaint several times per frame. Instead each handler
        updates a little view state that _refresh() then composes through Img.
        """
        handler = self._event_handlers.get(type(event))
        if handler is not None:
            handler(event)

    @staticmethod
    def _blank_scores() -> Dict[str, int]:
        return {color: consts.STARTING_SCORE for color in consts.ALL_COLORS}

    def _on_piece_captured(self, event: PieceCapturedEvent) -> None:
        self._capture_flashes.append(_CaptureFlash(pos=event.pos, started_ms=event.at_ms))

    def _on_score_updated(self, event: ScoreUpdatedEvent) -> None:
        self._scores = {
            consts.COLOR_WHITE: event.white_score,
            consts.COLOR_BLACK: event.black_score,
        }

    def _on_game_ended(self, event: GameEndedEvent) -> None:
        self._pending_game_over = event

    def _on_game_started(self, event: GameStartedEvent) -> None:
        self._capture_flashes.clear()
        self._scores = self._blank_scores()
        self._pending_game_over = None

    def _build_settings_vars(self) -> None:
        """Load saved user settings into the tk variables the menus bind to."""
        self._settings = self.settings_store.load()
        self._piece_theme_var = tk.StringVar(master=self.root, value=self._settings.piece_theme)
        self._board_theme_var = tk.StringVar(master=self.root, value=self._settings.board_theme)
        self._speed_var = tk.IntVar(master=self.root, value=self._settings.speed_level_ms)
        self._cooldown_var = tk.IntVar(master=self.root, value=self._settings.cooldown_level_ms)

    def _build_canvas(self, board_size: int) -> None:
        """Create the canvas, its image view, and the click/resize bindings."""
        self.canvas_width = consts.SIDE_PANEL_WIDTH * 2 + board_size
        self.canvas_height = consts.PANEL_TOP_HEIGHT + board_size
        self.canvas = tk.Canvas(
            self.root, width=self.canvas_width, height=self.canvas_height, highlightthickness=0
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        canvas_image_id = self.canvas.create_image(0, 0, anchor="nw")
        self.view = TkImageView(self.canvas, canvas_image_id)
        self.renderer.resize(board_size, board_size)

        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Button-1>", self._on_left_click)
        self.canvas.bind("<Button-3>", self._on_right_click)

    def _start_tick_loop(self) -> None:
        """Draw the first frame and begin the render loop."""
        self._last_tick = time.monotonic()
        self._refresh()
        self._schedule_tick()

    def _build_menu(self) -> None:
        """Build the Game and Settings menu bar."""
        menu_bar = tk.Menu(self.root)

        game_menu = tk.Menu(menu_bar, tearoff=0)
        game_menu.add_command(label="Save History...", command=self._save_history)
        game_menu.add_command(label="Load History...", command=self._load_history)
        menu_bar.add_cascade(label="Game", menu=game_menu)

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
        self._add_radio_submenu(
            settings_menu, "Movement Speed",
            list(consts.SPEED_PRESETS_MS.items()), self._speed_var, self._on_speed_selected,
        )
        self._add_radio_submenu(
            settings_menu, "Cooldown Time",
            list(consts.COOLDOWN_PRESETS_MS.items()), self._cooldown_var,
            self._on_cooldown_selected,
        )
        menu_bar.add_cascade(label="Settings", menu=settings_menu)

        self.root.config(menu=menu_bar)

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

    def _save_history(self) -> None:
        prompt_and_save(self.root, self.service, self.white_name, self.black_name, None)

    def _load_history(self) -> None:
        show_load_history_dialog(self.root, self.service, self._build_replay_renderer)

    def _build_replay_renderer(self) -> PillowRenderer:
        """A renderer for a replay window, themed like the live board but
        independent of it — the two size their boards separately."""
        theme = get_theme(self._settings.piece_theme)
        sprite_path = os.path.join(self.assets_dir, theme.folder_name) if self.assets_dir else ""
        renderer = PillowRenderer(sprite_path)
        board_theme = get_board_theme(self._settings.board_theme)
        renderer.set_board_theme(board_theme.light_color, board_theme.dark_color)
        return renderer

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
        self.service.update_preferences(self._settings.speed_level_ms, self._settings.cooldown_level_ms)

    def _on_cooldown_selected(self, cooldown_ms: int) -> None:
        self._save_settings(replace(self._settings, cooldown_level_ms=cooldown_ms))
        self.service.update_preferences(self._settings.speed_level_ms, self._settings.cooldown_level_ms)

    def _save_settings(self, settings: UserSettings) -> None:
        self._settings = settings
        self.settings_store.save(settings)

    def run(self) -> None:
        self.root.mainloop()

    def _canvas_to_cell(self, event_x: int, event_y: int) -> Optional[tuple[int, int]]:
        panel_width = consts.SIDE_PANEL_WIDTH
        top_height = consts.PANEL_TOP_HEIGHT
        board_x_offset = panel_width + (self.canvas_width - panel_width * 2 - self.board_size) // 2
        board_y_offset = top_height + (self.canvas_height - top_height - self.board_size) // 2

        board_x = event_x - board_x_offset
        board_y = event_y - board_y_offset
        return self.renderer.get_geometry().pixel_to_cell(board_x, board_y)

    def _on_left_click(self, event) -> None:
        cell = self._canvas_to_cell(event.x, event.y)
        if cell is None:
            return
        row, col = cell
        self.service.click(row, col)
        self._refresh()

    def _on_right_click(self, event) -> None:
        """Jump the piece under the cursor in place, regardless of current selection."""
        cell = self._canvas_to_cell(event.x, event.y)
        if cell is None:
            return
        row, col = cell
        self.service.right_click(row, col)
        self._refresh()

    def _on_resize(self, event) -> None:
        if event.widget == self.canvas:
            self.canvas_width = event.width
            self.canvas_height = event.height
            
            minimum = consts.MIN_BOARD_DIMENSION_PX
            available_board_w = max(minimum, self.canvas_width - consts.SIDE_PANEL_WIDTH * 2)
            available_board_h = max(minimum, self.canvas_height - consts.PANEL_TOP_HEIGHT)
            self.board_size = min(available_board_w, available_board_h)


            self.renderer.resize(self.board_size, self.board_size)
            self._refresh()

    def _schedule_tick(self) -> None:
        self.root.after(consts.TICK_MS, self._tick)

    def _tick(self) -> None:
        now = time.monotonic()
        elapsed_ms = int((now - self._last_tick) * consts.MS_PER_SECOND)
        self._last_tick = now

        self.service.advance_clock(elapsed_ms)
        self._refresh()
        self._schedule_tick()

    def _refresh(self) -> None:
        snapshot = self.service.get_snapshot()
        if snapshot is None:
            return
        self.renderer.draw(snapshot)

        board_img = self.renderer.get_image()
        self._draw_capture_flashes(board_img, snapshot.clock_ms)
        composed = self.info_panel.render(
            board_img,
            self.board_size,
            self.canvas_width,
            self.canvas_height,
            self.service.get_moves(),
            white_score=self._scores[consts.COLOR_WHITE],
            black_score=self._scores[consts.COLOR_BLACK],
        )
        self.view.show(composed)

        self._prompt_save_if_game_ended()

    def _draw_capture_flashes(self, board_img: Img, clock_ms: int) -> None:
        """Paint and age the capture markers collected by on_event.

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
                (*consts.CAPTURE_FLASH_COLOR, flash.alpha_at(clock_ms)),
            )

    def _prompt_save_if_game_ended(self) -> None:
        """Offer to save the finished game, once, for the ending the engine announced."""
        if self._pending_game_over is None:
            return
        winner = self._pending_game_over.winner
        self._pending_game_over = None
        self.root.after(0, lambda: self._save_history_with_winner(winner))

    def _save_history_with_winner(self, winner) -> None:
        prompt_and_save(self.root, self.service, self.white_name, self.black_name, winner)
