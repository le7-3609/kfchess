"""Lobby / Home Screen UI Window (Layer 6 / Client UI).

Owns: the home screen presentation after authentication, offering:
  - "Play" (Matchmaking queue search with ELO ±100, 1-min timeout popup, and cancel search)
  - "Room" (Popup dialog with text box for room_id, Create/Join/Cancel buttons)
  - "Offline" (a local match, either two players sharing the machine or one
    against the bot — no server process and no socket)
Must not own: socket transport or game engine simulation logic.

This is the one place that decides which IGameController a match runs on. The
window it launches is the same class either way, which is what makes the two
modes indistinguishable from the rendering side.
"""

import os
import queue
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, Optional

from shared.config import consts
from shared.config.bot_profile import BotDifficulty, BotProfile
from shared.input.bot_strategy import BotStrategyInterface
from client.ai.llm_strategy import build_llm_strategy
from client.ai.providers import active_provider, load_api_key
from client.auth.cli_auth import UserCredentials
from client.controllers.game_controller import IGameController
from client.controllers.local_game_controller import build_bot_controller, build_hotseat_controller
from client.controllers.network_game_controller import NetworkGameController
from client.network import protocol
from client.network.network_client import NetworkClient
from client.ui import consts as ui_consts
from client.ui.preferences.board_themes import get_theme as get_board_theme
from client.ui.preferences.piece_themes import get_theme as get_piece_theme
from client.ui.preferences.user_settings_store import UserSettingsStore
from client.ui.rendering.pillow_renderer import PillowRenderer
from client.ui.window.game_window import GameWindow


class LobbyWindow:
    """Home screen / Lobby interface for KungFu Chess."""

    def __init__(
        self,
        server_url: str,
        credentials: UserCredentials,
        assets_dir: str,
        settings_store: Optional[UserSettingsStore] = None,
    ) -> None:
        self.server_url = server_url
        self.credentials = credentials
        self.assets_dir = assets_dir
        self.settings_store = settings_store or UserSettingsStore()

        self.root = tk.Tk()
        self.root.title(f"{ui_consts.WINDOW_TITLE} — Home Screen")
        self.root.geometry(ui_consts.LOBBY_GEOMETRY)
        self.root.resizable(False, False)

        self._network_client: Optional[NetworkClient] = None
        self._message_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._search_start_time: float = 0.0
        self._is_searching: bool = False
        self._search_dialog: Optional[tk.Toplevel] = None

        self._build_ui()

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self.root, padding=ui_consts.LOBBY_FRAME_PADDING)
        main_frame.pack(fill=tk.BOTH, expand=True)

        title_label = ttk.Label(
            main_frame,
            text="KungFu Chess",
            font=(ui_consts.UI_FONT_FAMILY, ui_consts.TITLE_FONT_SIZE, ui_consts.FONT_WEIGHT_BOLD),
        )
        title_label.pack(pady=(0, ui_consts.SPACING_SM))

        user_info = f"Player: {self.credentials.username}  |  ELO: {self.credentials.elo}"
        info_label = ttk.Label(
            main_frame,
            text=user_info,
            font=(ui_consts.UI_FONT_FAMILY, ui_consts.BODY_FONT_SIZE),
        )
        info_label.pack(pady=(0, ui_consts.SPACING_SECTION))

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.BOTH, expand=True)

        play_btn = ttk.Button(
            btn_frame,
            text="⚔️ Play (Matchmaking)",
            command=self._on_play_clicked,
        )
        play_btn.pack(fill=tk.X, pady=ui_consts.LOBBY_BUTTON_PAD_Y, ipady=ui_consts.LOBBY_BUTTON_IPAD_Y)

        room_btn = ttk.Button(
            btn_frame,
            text="🚪 Room (Create / Join)",
            command=self._on_room_clicked,
        )
        room_btn.pack(fill=tk.X, pady=ui_consts.LOBBY_BUTTON_PAD_Y, ipady=ui_consts.LOBBY_BUTTON_IPAD_Y)

        offline_btn = ttk.Button(
            btn_frame,
            text="💻 Offline (Local / vs Bot)",
            command=self._on_offline_clicked,
        )
        offline_btn.pack(fill=tk.X, pady=ui_consts.LOBBY_BUTTON_PAD_Y, ipady=ui_consts.LOBBY_BUTTON_IPAD_Y)

        exit_btn = ttk.Button(
            btn_frame,
            text="Exit",
            command=self.root.destroy,
        )
        exit_btn.pack(fill=tk.X, pady=ui_consts.LOBBY_BUTTON_PAD_Y, ipady=ui_consts.EXIT_BUTTON_IPAD_Y)

    def run(self) -> None:
        self.root.mainloop()

    def _start_client(self, action: str, room_id: Optional[str] = None) -> NetworkClient:
        if self._network_client is not None:
            self._network_client.stop()

        client = NetworkClient(
            server_url=self.server_url,
            username=self.credentials.username,
            password=self.credentials.password,
            initial_action=action,
            room_id=room_id,
        )
        self._network_client = client
        return client

    # --- Matchmaking Flow ("Play") ---

    def _on_play_clicked(self) -> None:
        client = self._start_client(action=protocol.MSG_TYPE_PLAY)
        self._message_queue = queue.Queue()
        client.start(on_message_callback=self._message_queue.put)

        self._is_searching = True
        self._search_start_time = time.time()
        self._show_search_dialog()
        self._poll_matchmaking_queue()

    def _show_search_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(ui_consts.MATCHMAKING_DIALOG_TITLE)
        dialog.geometry(ui_consts.MATCHMAKING_DIALOG_GEOMETRY)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        self._search_dialog = dialog

        lbl = ttk.Label(
            dialog,
            text="Searching for an opponent\n(ELO ±100 range)...",
            font=(ui_consts.UI_FONT_FAMILY, ui_consts.BODY_FONT_SIZE),
            justify=tk.CENTER,
        )
        lbl.pack(pady=(ui_consts.SPACING_SECTION, ui_consts.SPACING_XXL))

        cancel_btn = ttk.Button(
            dialog,
            text="Cancel Search",
            command=self._cancel_search,
        )
        cancel_btn.pack(pady=ui_consts.SPACING_SM)

    def _cancel_search(self) -> None:
        self._is_searching = False
        if self._search_dialog:
            self._search_dialog.destroy()
            self._search_dialog = None
        if self._network_client:
            self._network_client.send_cancel_search()
            self._network_client.stop()
            self._network_client = None

    def _poll_matchmaking_queue(self) -> None:
        if not self._is_searching:
            return

        while True:
            try:
                msg = self._message_queue.get_nowait()
            except queue.Empty:
                break
            
            msg_type = msg.get(protocol.FIELD_TYPE)
            if msg_type == protocol.MSG_TYPE_GAME_START:
                self._is_searching = False
                if self._search_dialog:
                    self._search_dialog.destroy()
                    self._search_dialog = None
                self._launch_online_game(self._network_client, msg)
                return
            elif msg_type == protocol.MSG_TYPE_ERROR:
                self._cancel_search()
                messagebox.showinfo(
                    ui_consts.MATCHMAKING_DIALOG_TITLE,
                    msg.get(protocol.FIELD_MESSAGE, "Search timed out."),
                )
                return

        if time.time() - self._search_start_time >= ui_consts.MATCHMAKING_TIMEOUT_SECONDS:
            self._cancel_search()
            messagebox.showinfo(
                ui_consts.MATCHMAKING_DIALOG_TITLE,
                "Could not find an opponent within 1 minute. Please try again later.",
            )
            return

        self.root.after(ui_consts.LOBBY_POLL_INTERVAL_MS, self._poll_matchmaking_queue)

    # --- Room Dialog Flow ("Room") ---

    def _on_room_clicked(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(ui_consts.ROOM_DIALOG_TITLE)
        dialog.geometry(ui_consts.ROOM_DIALOG_GEOMETRY)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        lbl = ttk.Label(
            dialog, text="Room ID:", font=(ui_consts.UI_FONT_FAMILY, ui_consts.BODY_FONT_SIZE)
        )
        lbl.pack(pady=(ui_consts.SPACING_XXL, ui_consts.SPACING_SM))

        room_entry = ttk.Entry(
            dialog, font=(ui_consts.UI_FONT_FAMILY, ui_consts.ENTRY_FONT_SIZE), justify=tk.CENTER
        )
        room_entry.pack(
            pady=ui_consts.SPACING_SM,
            ipadx=ui_consts.ROOM_ENTRY_IPAD_X,
            ipady=ui_consts.SPACING_XS,
        )
        room_entry.focus_set()

        btn_box = ttk.Frame(dialog)
        btn_box.pack(pady=ui_consts.SPACING_XXL)

        create_btn = ttk.Button(
            btn_box,
            text="Create",
            command=lambda: self._handle_room_action(dialog, protocol.MSG_TYPE_CREATE_ROOM, None),
        )
        create_btn.pack(side=tk.LEFT, padx=ui_consts.SPACING_SM)

        join_btn = ttk.Button(
            btn_box,
            text="Join",
            command=lambda: self._handle_room_action(
                dialog, protocol.MSG_TYPE_JOIN_ROOM, room_entry.get().strip().upper()
            ),
        )
        join_btn.pack(side=tk.LEFT, padx=ui_consts.SPACING_SM)

        cancel_btn = ttk.Button(
            btn_box,
            text="Cancel",
            command=dialog.destroy,
        )
        cancel_btn.pack(side=tk.LEFT, padx=ui_consts.SPACING_SM)

    def _handle_room_action(
        self, dialog: tk.Toplevel, action: str, room_id: Optional[str]
    ) -> None:
        if action == protocol.MSG_TYPE_JOIN_ROOM and not room_id:
            messagebox.showwarning(ui_consts.ROOM_DIALOG_TITLE, "Please enter a Room ID to join.")
            return

        dialog.destroy()
        client = self._start_client(action=action, room_id=room_id)
        self._message_queue = queue.Queue()
        client.start(on_message_callback=self._message_queue.put)

        self._poll_room_start()

    def _poll_room_start(self) -> None:
        while True:
            try:
                msg = self._message_queue.get_nowait()
            except queue.Empty:
                break

            msg_type = msg.get(protocol.FIELD_TYPE)
            if msg_type in (protocol.MSG_TYPE_GAME_START, protocol.MSG_TYPE_ROOM_CREATED):
                self._launch_online_game(self._network_client, msg)
                return
            elif msg_type == protocol.MSG_TYPE_ERROR:
                messagebox.showerror(
                    "Room Error",
                    msg.get(protocol.FIELD_MESSAGE, "Failed to process room action."),
                )
                if self._network_client:
                    self._network_client.stop()
                    self._network_client = None
                return

        self.root.after(ui_consts.LOBBY_POLL_INTERVAL_MS, self._poll_room_start)

    # --- Offline Flow ---

    def _on_offline_clicked(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title(ui_consts.OFFLINE_DIALOG_TITLE)
        dialog.geometry(ui_consts.OFFLINE_DIALOG_GEOMETRY)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog,
            text="Play without a server",
            font=(
                ui_consts.UI_FONT_FAMILY,
                ui_consts.SECTION_TITLE_FONT_SIZE,
                ui_consts.FONT_WEIGHT_BOLD,
            ),
        ).pack(pady=(ui_consts.SPACING_TITLE, ui_consts.SPACING_MD))

        ttk.Button(
            dialog,
            text="👥 Two Players (same machine)",
            command=lambda: self._start_offline_game(dialog, self._build_hotseat_controller),
        ).pack(fill=tk.X, padx=ui_consts.SPACING_SECTION, pady=(0, ui_consts.SPACING_XL))

        self._build_bot_setup_panel(dialog)

    def _build_bot_setup_panel(self, dialog: tk.Toplevel) -> None:
        """The vs-bot configuration: opponent strength, speed, and which side to take."""
        panel = ttk.LabelFrame(dialog, text="🤖 Play vs Bot", padding=ui_consts.BOT_PANEL_PADDING)
        panel.pack(fill=tk.X, padx=ui_consts.SPACING_SECTION, pady=(0, ui_consts.SPACING_XL))

        llm_provider = active_provider()
        llm_available = load_api_key(llm_provider) is not None
        difficulty_var = tk.StringVar(value=BotDifficulty.GREEDY.value)
        self._build_radio_row(
            panel,
            "Difficulty:",
            difficulty_var,
            (
                (BotDifficulty.GREEDY.value, "Greedy"),
                (BotDifficulty.RANDOM.value, "Random"),
                (BotDifficulty.LLM.value, llm_provider.label),
            ),
            disabled_values=() if llm_available else (BotDifficulty.LLM.value,),
        )
        if not llm_available:
            ttk.Label(
                panel,
                text=(
                    f"{llm_provider.label} needs {llm_provider.api_key_var} in .env "
                    "(see .env.example)"
                ),
                foreground=ui_consts.DISABLED_TEXT_COLOR,
            ).pack(anchor=tk.W)

        speed_var = tk.StringVar(value=ui_consts.DEFAULT_BOT_SPEED_PRESET)
        self._build_radio_row(
            panel,
            "Bot speed:",
            speed_var,
            tuple((name, name) for name in ui_consts.BOT_SPEED_PRESETS_MS),
        )

        color_var = tk.StringVar(value=consts.COLOR_WHITE)
        self._build_radio_row(
            panel,
            "Play as:",
            color_var,
            (
                (consts.COLOR_WHITE, ui_consts.COLOR_DISPLAY_NAMES[consts.COLOR_WHITE]),
                (consts.COLOR_BLACK, ui_consts.COLOR_DISPLAY_NAMES[consts.COLOR_BLACK]),
            ),
        )

        ttk.Button(
            panel,
            text="Start vs Bot",
            command=lambda: self._start_offline_game(
                dialog,
                lambda: self._build_bot_controller(
                    color_var.get(), difficulty_var.get(), speed_var.get()
                ),
            ),
        ).pack(fill=tk.X, pady=(ui_consts.SPACING_LG, 0))

    def _build_radio_row(self, parent, label, variable, options, disabled_values=()) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=ui_consts.SPACING_XS)
        ttk.Label(row, text=label, width=ui_consts.RADIO_LABEL_WIDTH).pack(side=tk.LEFT)
        for value, text in options:
            button = ttk.Radiobutton(row, text=text, value=value, variable=variable)
            if value in disabled_values:
                button.state([ui_consts.WIDGET_STATE_DISABLED])
            button.pack(side=tk.LEFT, padx=ui_consts.RADIO_BUTTON_PAD_X)

    def _start_offline_game(self, dialog: tk.Toplevel, controller_factory) -> None:
        dialog.destroy()
        self._launch_game_window(controller_factory())

    def _build_hotseat_controller(self) -> IGameController:
        settings = self.settings_store.load()
        return build_hotseat_controller(settings.speed_level_ms, settings.cooldown_level_ms)

    def _build_bot_controller(
        self, player_color: str, difficulty: str, speed_preset: str
    ) -> IGameController:
        settings = self.settings_store.load()
        bot_difficulty = BotDifficulty(difficulty)
        profile = BotProfile(
            difficulty=bot_difficulty,
            move_interval_ms=ui_consts.BOT_SPEED_PRESETS_MS[speed_preset],
        )
        return build_bot_controller(
            player_color,
            settings.speed_level_ms,
            settings.cooldown_level_ms,
            bot_profile=profile,
            bot_strategy=self._build_bot_strategy(bot_difficulty, player_color),
        )

    def _build_bot_strategy(
        self, difficulty: BotDifficulty, player_color: str
    ) -> Optional[BotStrategyInterface]:
        """Strategies shared/ cannot compose itself — today only the LLM one."""
        if difficulty is not BotDifficulty.LLM:
            return None
        return build_llm_strategy(consts.opponent_color(player_color))

    # --- Shared launch path ---

    def _launch_online_game(self, client: NetworkClient, start_msg: Dict[str, Any]) -> None:
        """Hand the running connection to a controller and open the game window.

        The start frame — and anything that landed behind it while this dialog
        was still up — is replayed into the controller before the window runs,
        so the seat assignment and any early `game_state` survive the handoff.
        """
        controller = NetworkGameController(
            network_client=client, username=self.credentials.username
        )
        controller.accept_frame(start_msg)
        self._forward_pending_frames(controller)
        self._launch_game_window(controller)

    def _forward_pending_frames(self, controller: NetworkGameController) -> None:
        """Replay any frames still sitting in the lobby's queue onto the controller.

        The network client keeps pushing onto the lobby's queue (game_state,
        events, ...) right up until `start()` redirects it, so a frame that
        lands in that gap must be forwarded here rather than dropped.
        """
        while True:
            try:
                message = self._message_queue.get_nowait()
            except queue.Empty:
                return
            controller.accept_frame(message)

    def _launch_game_window(self, controller: IGameController) -> None:
        """Open the game window on *controller* and hide the lobby behind it."""
        self.root.withdraw()

        game_win = GameWindow(
            controller=controller,
            renderer=self._build_renderer(),
            username=self.credentials.username,
            assets_dir=self.assets_dir,
            settings_store=self.settings_store,
        )
        game_win.root.protocol(
            ui_consts.WM_DELETE_WINDOW_PROTOCOL, lambda: self._on_game_window_closed(game_win)
        )
        game_win.run()

    def _build_renderer(self) -> PillowRenderer:
        settings = self.settings_store.load()
        piece_theme = get_piece_theme(settings.piece_theme)
        board_theme = get_board_theme(settings.board_theme)

        renderer = PillowRenderer(os.path.join(self.assets_dir, piece_theme.folder_name))
        renderer.set_board_theme(board_theme.light_color, board_theme.dark_color)
        return renderer

    def _on_game_window_closed(self, game_win: GameWindow) -> None:
        game_win.close()
        self.root.deiconify()
