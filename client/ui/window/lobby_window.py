"""Lobby / Home Screen UI Window (Layer 6 / Client UI).

Owns: the home screen presentation after authentication, offering:
  - "Play" (Matchmaking queue search with ELO ±100, 1-min timeout popup, and cancel search)
  - "Room" (Popup dialog with text box for room_id, Create/Join/Cancel buttons)
Must not own: socket transport or game engine simulation logic.
"""

import os
import queue
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Dict, Optional

from client.cli_auth import UserCredentials
from client.network_client import NetworkClient
from client.ui import consts as ui_consts
from client.ui.preferences.user_settings_store import UserSettingsStore
from client.ui.rendering.pillow_renderer import PillowRenderer
from client.ui.window.networked_game_window import NetworkedGameWindow


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
        self.root.geometry("460x320")
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
                self._launch_game_window(self._network_client, msg)
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
                self._launch_game_window(self._network_client, msg)
                return
            elif msg_type == "error":
                messagebox.showerror("Room Error", msg.get("message", "Failed to process room action."))
                if self._network_client:
                    self._network_client.stop()
                    self._network_client = None
                return

        self.root.after(200, self._poll_room_start)

    def _launch_game_window(self, client: NetworkClient, start_msg: Dict[str, Any]) -> None:
        self.root.withdraw()

        settings = self.settings_store.load()
        from client.ui.preferences.piece_themes import get_theme
        from client.ui.preferences.board_themes import get_theme as get_board_theme

        piece_theme = get_theme(settings.piece_theme)
        board_theme = get_board_theme(settings.board_theme)

        renderer = PillowRenderer(os.path.join(self.assets_dir, piece_theme.folder_name))
        renderer.set_board_theme(board_theme.light_color, board_theme.dark_color)

        game_win = NetworkedGameWindow(
            network_client=client,
            renderer=renderer,
            username=self.credentials.username,
            assets_dir=self.assets_dir,
            settings_store=self.settings_store,
        )

        # Pre-feed the game_start message so color/title/opponent are immediately populated
        game_win._handle_message(start_msg)
        self._forward_pending_frames(game_win)

        game_win.root.protocol("WM_DELETE_WINDOW", lambda: self._on_game_window_closed(game_win))
        game_win.attach_and_run()

    def _forward_pending_frames(self, game_win: NetworkedGameWindow) -> None:
        """Replay any frames still sitting in the lobby's queue onto the game window.

        The network client keeps pushing onto the lobby's queue (game_state,
        events, ...) right up until `attach_and_run` redirects it, so a frame
        that lands in that gap must be forwarded here rather than dropped.
        """
        while True:
            try:
                message = self._message_queue.get_nowait()
            except queue.Empty:
                return
            game_win._handle_message(message)

    def _on_game_window_closed(self, game_win: NetworkedGameWindow) -> None:
        game_win._on_close()
        self.root.deiconify()
