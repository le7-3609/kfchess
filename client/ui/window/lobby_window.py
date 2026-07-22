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
        self.root.geometry("460x380")
        self.root.resizable(False, False)

        self._network_client: Optional[NetworkClient] = None
        self._message_queue: "queue.Queue[Dict[str, Any]]" = queue.Queue()
        self._search_start_time: float = 0.0
        self._is_searching: bool = False
        self._search_dialog: Optional[tk.Toplevel] = None

        self._build_ui()

    def _build_ui(self) -> None:
        main_frame = ttk.Frame(self.root, padding="20 20 20 20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        title_label = ttk.Label(
            main_frame,
            text="KungFu Chess",
            font=("Helvetica", 20, "bold"),
        )
        title_label.pack(pady=(0, 5))

        user_info = f"Player: {self.credentials.username}  |  ELO: {self.credentials.elo}"
        info_label = ttk.Label(
            main_frame,
            text=user_info,
            font=("Helvetica", 11),
        )
        info_label.pack(pady=(0, 20))

        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.BOTH, expand=True)

        play_btn = ttk.Button(
            btn_frame,
            text="⚔️ Play (Matchmaking)",
            command=self._on_play_clicked,
        )
        play_btn.pack(fill=tk.X, pady=8, ipady=6)

        room_btn = ttk.Button(
            btn_frame,
            text="🚪 Room (Create / Join)",
            command=self._on_room_clicked,
        )
        room_btn.pack(fill=tk.X, pady=8, ipady=6)

        offline_btn = ttk.Button(
            btn_frame,
            text="💻 Offline (Local / vs Bot)",
            command=self._on_offline_clicked,
        )
        offline_btn.pack(fill=tk.X, pady=8, ipady=6)

        exit_btn = ttk.Button(
            btn_frame,
            text="Exit",
            command=self.root.destroy,
        )
        exit_btn.pack(fill=tk.X, pady=8, ipady=4)

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
        client = self._start_client(action="play")
        self._message_queue = queue.Queue()
        client.start(on_message_callback=self._message_queue.put)

        self._is_searching = True
        self._search_start_time = time.time()
        self._show_search_dialog()
        self._poll_matchmaking_queue()

    def _show_search_dialog(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Matchmaking")
        dialog.geometry("340x160")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        self._search_dialog = dialog

        lbl = ttk.Label(
            dialog,
            text=f"Searching for an opponent\n(ELO ±100 range)...",
            font=("Helvetica", 11),
            justify=tk.CENTER,
        )
        lbl.pack(pady=(20, 15))

        cancel_btn = ttk.Button(
            dialog,
            text="Cancel Search",
            command=self._cancel_search,
        )
        cancel_btn.pack(pady=5)

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
            
            msg_type = msg.get("type")
            if msg_type == "game_start":
                self._is_searching = False
                if self._search_dialog:
                    self._search_dialog.destroy()
                    self._search_dialog = None
                self._launch_online_game(self._network_client, msg)
                return
            elif msg_type == "error":
                self._cancel_search()
                messagebox.showinfo("Matchmaking", msg.get("message", "Search timed out."))
                return

        # 60-second client-side fallback timeout check
        if time.time() - self._search_start_time >= 60.0:
            self._cancel_search()
            messagebox.showinfo(
                "Matchmaking",
                "Could not find an opponent within 1 minute. Please try again later.",
            )
            return

        self.root.after(200, self._poll_matchmaking_queue)

    # --- Room Dialog Flow ("Room") ---

    def _on_room_clicked(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Room")
        dialog.geometry("360x200")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        lbl = ttk.Label(dialog, text="Room ID:", font=("Helvetica", 11))
        lbl.pack(pady=(15, 5))

        room_entry = ttk.Entry(dialog, font=("Helvetica", 12), justify=tk.CENTER)
        room_entry.pack(pady=5, ipadx=10, ipady=3)
        room_entry.focus_set()

        btn_box = ttk.Frame(dialog)
        btn_box.pack(pady=15)

        create_btn = ttk.Button(
            btn_box,
            text="Create",
            command=lambda: self._handle_room_action(dialog, "create_room", None),
        )
        create_btn.pack(side=tk.LEFT, padx=5)

        join_btn = ttk.Button(
            btn_box,
            text="Join",
            command=lambda: self._handle_room_action(
                dialog, "join_room", room_entry.get().strip().upper()
            ),
        )
        join_btn.pack(side=tk.LEFT, padx=5)

        cancel_btn = ttk.Button(
            btn_box,
            text="Cancel",
            command=dialog.destroy,
        )
        cancel_btn.pack(side=tk.LEFT, padx=5)

    def _handle_room_action(
        self, dialog: tk.Toplevel, action: str, room_id: Optional[str]
    ) -> None:
        if action == "join_room" and not room_id:
            messagebox.showwarning("Room", "Please enter a Room ID to join.")
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

            msg_type = msg.get("type")
            if msg_type in ("game_start", "room_created"):
                self._launch_online_game(self._network_client, msg)
                return
            elif msg_type == "error":
                messagebox.showerror("Room Error", msg.get("message", "Failed to process room action."))
                if self._network_client:
                    self._network_client.stop()
                    self._network_client = None
                return

        self.root.after(200, self._poll_room_start)

    # --- Offline Flow ---

    def _on_offline_clicked(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Offline Game")
        dialog.geometry("420x440")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        ttk.Label(
            dialog, text="Play without a server", font=("Helvetica", 12, "bold")
        ).pack(pady=(18, 8))

        ttk.Button(
            dialog,
            text="👥 Two Players (same machine)",
            command=lambda: self._start_offline_game(dialog, self._build_hotseat_controller),
        ).pack(fill=tk.X, padx=20, pady=(0, 12))

        self._build_bot_setup_panel(dialog)

    def _build_bot_setup_panel(self, dialog: tk.Toplevel) -> None:
        """The vs-bot configuration: opponent strength, speed, and which side to take."""
        panel = ttk.LabelFrame(dialog, text="🤖 Play vs Bot", padding="12 8 12 12")
        panel.pack(fill=tk.X, padx=20, pady=(0, 12))

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
                foreground="gray",
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
            ((consts.COLOR_WHITE, "White"), (consts.COLOR_BLACK, "Black")),
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
        ).pack(fill=tk.X, pady=(10, 0))

    def _build_radio_row(self, parent, label, variable, options, disabled_values=()) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text=label, width=10).pack(side=tk.LEFT)
        for value, text in options:
            button = ttk.Radiobutton(row, text=text, value=value, variable=variable)
            if value in disabled_values:
                button.state(["disabled"])
            button.pack(side=tk.LEFT, padx=4)

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
        game_win.root.protocol("WM_DELETE_WINDOW", lambda: self._on_game_window_closed(game_win))
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
