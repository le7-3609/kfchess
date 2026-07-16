"""Save/Load History dialogs (UI window layer) — tkinter prompts that drive
the GameService history endpoint. This layer owns only widgets and event
routing; all persistence goes through the service facade, never the store.
"""

import tkinter as tk
from tkinter import messagebox, simpledialog
from typing import Optional

from kungfu_chess.io.game_history_store import SavedGame
from kungfu_chess.service import GameService

_COLOR_NAMES = {"w": "White", "b": "Black"}


def _format_time(millis: int) -> str:
    total_seconds = millis // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    ms = millis % 1000
    return f"{minutes:02d}:{seconds:02d}.{ms:03d}"


def prompt_and_save(
    parent: tk.Tk,
    service: GameService,
    white_name: str,
    black_name: str,
    winner: Optional[str],
) -> None:
    """Asks for a save name and writes the moves-so-far to disk via the service.
    Used both for the automatic prompt on game over (winner not None) and the
    manual "Save History..." menu action (winner None - game may still be
    ongoing).
    """
    winner_name = _COLOR_NAMES.get(winner, winner) if winner else None
    message = f"Game over - {winner_name} wins!\nSave this game as:" if winner_name else "Save this game as:"
    save_name = simpledialog.askstring("Save History", message, parent=parent)
    if save_name is None:
        return  # user cancelled
    if not save_name.strip():
        save_name = f"{white_name}_vs_{black_name}"

    file_path = service.save_history(save_name, white_name, black_name, winner)
    messagebox.showinfo("Save History", f"Saved to {file_path}", parent=parent)


def show_load_history_dialog(parent: tk.Tk, service: GameService) -> None:
    """Lists every saved game file and, on selection, shows its full move list."""
    saves = service.list_saves()
    if not saves:
        messagebox.showinfo("Load History", "No saved games yet.", parent=parent)
        return

    picker = tk.Toplevel(parent)
    picker.title("Load History")
    picker.transient(parent)

    tk.Label(picker, text="Select a saved game:").pack(padx=10, pady=(10, 0))

    listbox = tk.Listbox(picker, width=50, height=min(15, len(saves)))
    for name in saves:
        listbox.insert(tk.END, name)
    listbox.pack(padx=10, pady=10)
    listbox.selection_set(0)

    def on_open() -> None:
        selection = listbox.curselection()
        if not selection:
            return
        chosen = saves[selection[0]]
        picker.destroy()
        _show_saved_game(parent, service.load_saved_game(chosen))

    button_row = tk.Frame(picker)
    button_row.pack(pady=(0, 10))
    tk.Button(button_row, text="Open", command=on_open).pack(side=tk.LEFT, padx=5)
    tk.Button(button_row, text="Cancel", command=picker.destroy).pack(side=tk.LEFT, padx=5)
    listbox.bind("<Double-Button-1>", lambda e: on_open())


def _show_saved_game(parent: tk.Tk, saved: SavedGame) -> None:
    window = tk.Toplevel(parent)
    window.title(f"History: {saved.save_name}")

    header = f"{saved.white_name} vs {saved.black_name}\nSaved: {saved.saved_at}"
    if saved.winner:
        header += f"   Winner: {_COLOR_NAMES.get(saved.winner, saved.winner)}"
    tk.Label(window, text=header, justify=tk.LEFT).pack(padx=10, pady=(10, 0), anchor="w")

    text_frame = tk.Frame(window)
    text_frame.pack(padx=10, pady=10, fill=tk.BOTH, expand=True)

    scrollbar = tk.Scrollbar(text_frame)
    scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    text = tk.Text(text_frame, width=50, height=20, yscrollcommand=scrollbar.set)
    for entry in saved.moves:
        color_name = _COLOR_NAMES.get(entry.color, entry.color)
        text.insert(tk.END, f"{_format_time(entry.time_ms)}  {color_name:<6} {entry.notation}\n")
    text.config(state=tk.DISABLED)
    text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scrollbar.config(command=text.yview)
