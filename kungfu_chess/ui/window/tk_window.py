"""Tkinter window — the UI window/controls layer.

Owns: the tkinter Tk root, canvas, menus/dialogs, translating mouse clicks
into GameService commands, and driving the render loop (advance clock ->
get snapshot -> draw -> show) once per tick. Every pixel shown came from an
Img composed by PillowRenderer; tkinter here only displays the already-
finished frame.

Talks to the application only through GameService: it submits commands
(click / right_click / advance_clock) and reads queries (get_snapshot /
get_moves / history). It never touches the GameEngine, the board/state
repositories, or the arbiter directly — that is the whole point of the
UI/application split. Selection state lives inside the engine and arrives
back through the snapshot; this layer keeps none of it.
"""

import os
import time
import tkinter as tk
from dataclasses import replace
from typing import Optional

from kungfu_chess.service import GameService
from kungfu_chess.ui.preferences.board_themes import BOARD_THEMES, get_theme as get_board_theme
from kungfu_chess.ui.preferences.piece_themes import PIECE_THEMES, get_theme
from kungfu_chess.ui.preferences.user_settings_store import UserSettings, UserSettingsStore
from kungfu_chess.ui.rendering.info_panel import SIDE_PANEL_WIDTH, TOP_HEIGHT, InfoPanel
from kungfu_chess.ui.rendering.pillow_renderer import PillowRenderer
from kungfu_chess.ui.window.history_dialog import prompt_and_save, show_load_history_dialog
from kungfu_chess.ui.window.image_view import TkImageView
from kungfu_chess.ui.window.window_consts import BOARD_SIZE, TICK_MS

SPEED_PRESETS_MS = {"Fast": 600, "Normal": 1000, "Slow": 1600}
COOLDOWN_PRESETS_MS = {"Fast": 600, "Normal": 1000, "Slow": 1600}


class TkGameWindow:
    """Owns the tkinter Tk root, canvas, click bindings, and tick loop."""

    def __init__(
        self,
        service: GameService,
        renderer: PillowRenderer,
        title: str = "Kung Fu Chess",
        board_size: int = BOARD_SIZE,
        white_name: str = "White",
        black_name: str = "Black",
        assets_dir: Optional[str] = None,
        settings_store: Optional[UserSettingsStore] = None,
    ):
        self.service = service
        self.renderer = renderer
        self.board_size = board_size
        self.white_name = white_name
        self.black_name = black_name
        self.info_panel = InfoPanel(self.white_name, self.black_name)
        self._game_over_prompted = False

        self.assets_dir = assets_dir
        self.settings_store = settings_store or UserSettingsStore()

        self.root = tk.Tk()
        self.root.title(title)

        self._settings = self.settings_store.load()
        self._piece_theme_var = tk.StringVar(master=self.root, value=self._settings.piece_theme)
        self._board_theme_var = tk.StringVar(master=self.root, value=self._settings.board_theme)
        self._speed_var = tk.IntVar(master=self.root, value=self._settings.speed_level_ms)
        self._cooldown_var = tk.IntVar(master=self.root, value=self._settings.cooldown_level_ms)

        self._build_menu()

        self.canvas_width = SIDE_PANEL_WIDTH * 2 + board_size
        self.canvas_height = TOP_HEIGHT + board_size
        self.canvas = tk.Canvas(self.root, width=self.canvas_width, height=self.canvas_height, highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<Configure>", self._on_resize)

        canvas_image_id = self.canvas.create_image(0, 0, anchor="nw")
        self.view = TkImageView(self.canvas, canvas_image_id)

        self.renderer.resize(board_size, board_size)

        self.canvas.bind("<Button-1>", self._on_left_click)
        self.canvas.bind("<Button-3>", self._on_right_click)

        self._last_tick = time.monotonic()
        self._refresh()
        self._schedule_tick()

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self.root)
        game_menu = tk.Menu(menu_bar, tearoff=0)
        game_menu.add_command(label="Save History...", command=self._save_history)
        game_menu.add_command(label="Load History...", command=self._load_history)
        menu_bar.add_cascade(label="Game", menu=game_menu)

        settings_menu = tk.Menu(menu_bar, tearoff=0)
        theme_menu = tk.Menu(settings_menu, tearoff=0)
        for theme in PIECE_THEMES:
            theme_menu.add_radiobutton(
                label=theme.display_name,
                value=theme.theme_id,
                variable=self._piece_theme_var,
                command=lambda t=theme.theme_id: self._on_piece_theme_selected(t),
            )
        settings_menu.add_cascade(label="Piece Theme", menu=theme_menu)

        board_theme_menu = tk.Menu(settings_menu, tearoff=0)
        for theme in BOARD_THEMES:
            board_theme_menu.add_radiobutton(
                label=theme.display_name,
                value=theme.theme_id,
                variable=self._board_theme_var,
                command=lambda t=theme.theme_id: self._on_board_theme_selected(t),
            )
        settings_menu.add_cascade(label="Board Theme", menu=board_theme_menu)

        speed_menu = tk.Menu(settings_menu, tearoff=0)
        for label, ms_per_square in SPEED_PRESETS_MS.items():
            speed_menu.add_radiobutton(
                label=label,
                value=ms_per_square,
                variable=self._speed_var,
                command=lambda ms=ms_per_square: self._on_speed_selected(ms),
            )
        settings_menu.add_cascade(label="Movement Speed", menu=speed_menu)

        cooldown_menu = tk.Menu(settings_menu, tearoff=0)
        for label, cooldown_ms in COOLDOWN_PRESETS_MS.items():
            cooldown_menu.add_radiobutton(
                label=label,
                value=cooldown_ms,
                variable=self._cooldown_var,
                command=lambda ms=cooldown_ms: self._on_cooldown_selected(ms),
            )
        settings_menu.add_cascade(label="Cooldown Time", menu=cooldown_menu)

        menu_bar.add_cascade(label="Settings", menu=settings_menu)

        self.root.config(menu=menu_bar)

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

    # -- input ------------------------------------------------------------

    def _canvas_to_cell(self, event_x: int, event_y: int) -> Optional[tuple[int, int]]:
        board_x_offset = SIDE_PANEL_WIDTH + (self.canvas_width - SIDE_PANEL_WIDTH * 2 - self.board_size) // 2
        board_y_offset = TOP_HEIGHT + (self.canvas_height - TOP_HEIGHT - self.board_size) // 2
        
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
            
            available_board_w = max(100, self.canvas_width - SIDE_PANEL_WIDTH * 2)
            available_board_h = max(100, self.canvas_height - TOP_HEIGHT)
            self.board_size = min(available_board_w, available_board_h)
            
            self.renderer.resize(self.board_size, self.board_size)
            self._refresh()

    # -- tick loop ----------------------------------------------------------

    def _schedule_tick(self) -> None:
        self.root.after(TICK_MS, self._tick)

    def _tick(self) -> None:
        now = time.monotonic()
        elapsed_ms = int((now - self._last_tick) * 1000)
        self._last_tick = now

        self.service.advance_clock(elapsed_ms)
        self._refresh()
        self._schedule_tick()

    # -- rendering ----------------------------------------------------------

    def _refresh(self) -> None:
        snapshot = self.service.get_snapshot()
        if snapshot is None:
            return
        self.renderer.draw(snapshot)
        composed = self.info_panel.render(self.renderer.get_image(), self.board_size, self.canvas_width, self.canvas_height, self.service.get_moves())
        self.view.show(composed)

        if snapshot.game_over and not self._game_over_prompted:
            self._game_over_prompted = True
            winner = snapshot.winner
            self.root.after(0, lambda: self._save_history_with_winner(winner))

    def _save_history_with_winner(self, winner) -> None:
        prompt_and_save(self.root, self.service, self.white_name, self.black_name, winner)
