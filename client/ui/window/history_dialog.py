"""Save/Load History dialogs (UI window layer) — tkinter prompts that drive
the match's history endpoint. This layer owns only widgets and event routing;
all persistence goes through the MatchHistoryPort it is handed, never the
store — and never the whole GameService, which a networked match has no local
instance of.
"""

import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Callable, Optional

from client.controllers.game_controller import MatchHistoryPort
from client.ui import consts as ui_consts
from client.ui.rendering.pillow_renderer import PillowRenderer
from client.ui.window.replay_window import TkReplayWindow


def prompt_and_save(
    parent: tk.Tk,
    service: MatchHistoryPort,
    white_name: str,
    black_name: str,
    winner: Optional[str],
) -> None:
    """Ask for a save name and write the moves-so-far to disk via the service.

    Used both for the automatic prompt on game over (*winner* not None) and the
    manual "Save History..." menu action (*winner* None — the game may still be
    ongoing). Dismissing the prompt returns None and saves nothing, whereas an
    empty name falls back to "<white>_vs_<black>".
    """
    winner_name = ui_consts.COLOR_DISPLAY_NAMES.get(winner, winner) if winner else None
    message = f"Game over - {winner_name} wins!\nSave this game as:" if winner_name else "Save this game as:"
    save_name = simpledialog.askstring("Save History", message, parent=parent)
    if save_name is None:
        return
    if not save_name.strip():
        save_name = f"{white_name}_vs_{black_name}"

    file_path = service.save_history(save_name, white_name, black_name, winner)
    messagebox.showinfo("Save History", f"Saved to {file_path}", parent=parent)


def show_load_history_dialog(
    parent: tk.Tk,
    service: MatchHistoryPort,
    renderer_factory: Callable[[], PillowRenderer],
) -> None:
    """Lists every saved game file and, on selection, replays it visually.

    *renderer_factory* builds the replay's own renderer, so playback picks up
    the player's current themes without sharing the game window's renderer.
    """
    saves = service.list_saves()
    if not saves:
        messagebox.showinfo("Load History", "No saved games yet.", parent=parent)
        return

    picker = tk.Toplevel(parent)
    picker.title("Load History")
    picker.transient(parent)

    listbox = _build_saves_listbox(picker, saves)

    def on_open() -> None:
        selection = listbox.curselection()
        if not selection:
            return
        chosen = saves[selection[0]]
        picker.destroy()
        TkReplayWindow(parent, service.load_saved_game(chosen), renderer_factory())

    _build_picker_buttons(picker, on_open)
    listbox.bind("<Double-Button-1>", lambda event: on_open())


def _build_saves_listbox(picker: tk.Toplevel, saves) -> tk.Listbox:
    """Fill *picker* with a labelled list of *saves*, preselecting the first."""
    tk.Label(picker, text="Select a saved game:").pack(padx=10, pady=(10, 0))
    listbox = tk.Listbox(picker, width=50, height=min(15, len(saves)))
    for name in saves:
        listbox.insert(tk.END, name)
    listbox.pack(padx=10, pady=10)
    listbox.selection_set(0)
    return listbox


def _build_picker_buttons(picker: tk.Toplevel, on_open: Callable[[], None]) -> None:
    """Add the Play/Cancel row to *picker*."""
    button_row = tk.Frame(picker)
    button_row.pack(pady=(0, 10))
    tk.Button(button_row, text="Play", command=on_open).pack(side=tk.LEFT, padx=5)
    tk.Button(button_row, text="Cancel", command=picker.destroy).pack(side=tk.LEFT, padx=5)
