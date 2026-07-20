"""Modal reconnection-status overlay (UI window layer) — a small Toplevel that
reports NetworkClient's connection state to the player without blocking the
Tk main loop. This layer owns only the widget and its lifecycle; it has no
knowledge of the network layer beyond the plain strings it is told to show.
"""

import tkinter as tk
from typing import Callable, Optional


class ReconnectOverlay:
    """Owns a lazily-created, modal Toplevel that reports connection status.

    `show`/`show_terminal`/`hide` are safe to call repeatedly and in any
    order: the Toplevel is created on first use and destroyed on `hide`, so a
    game that never disconnects never pays for a hidden window. `grab_set()`
    makes it modal — the board underneath is frozen on a stale snapshot while
    disconnected, so blocking clicks prevents moves against it.
    """

    def __init__(self, parent: tk.Tk) -> None:
        self._parent = parent
        self._window: Optional[tk.Toplevel] = None
        self._message_label: Optional[tk.Label] = None
        self._close_button: Optional[tk.Button] = None

    def show(self, message: str) -> None:
        """Display *message* with no dismiss action — recovery is still in progress."""
        window = self._ensure_window()
        self._message_label.config(text=message)
        self._close_button.pack_forget()
        self._center(window)

    def show_terminal(self, message: str, on_close: Callable[[], None]) -> None:
        """Display a non-recoverable *message* with a Close button wired to *on_close*."""
        window = self._ensure_window()
        self._message_label.config(text=message)
        self._close_button.config(command=on_close)
        self._close_button.pack(pady=(0, 12))
        window.protocol("WM_DELETE_WINDOW", on_close)
        self._center(window)

    def hide(self) -> None:
        if self._window is None:
            return
        self._window.grab_release()
        self._window.destroy()
        self._window = None
        self._message_label = None
        self._close_button = None

    def _ensure_window(self) -> tk.Toplevel:
        if self._window is not None:
            return self._window

        window = tk.Toplevel(self._parent)
        window.title("Connection")
        window.resizable(False, False)
        window.protocol("WM_DELETE_WINDOW", lambda: None)  # no dismiss while recovery is possible

        self._message_label = tk.Label(window, text="", padx=24, pady=16, justify="center")
        self._message_label.pack()
        self._close_button = tk.Button(window, text="Close")

        window.transient(self._parent)
        window.grab_set()
        self._window = window
        return window

    def _center(self, window: tk.Toplevel) -> None:
        self._parent.update_idletasks()
        window.update_idletasks()
        x = self._parent.winfo_rootx() + (self._parent.winfo_width() - window.winfo_reqwidth()) // 2
        y = self._parent.winfo_rooty() + (self._parent.winfo_height() - window.winfo_reqheight()) // 2
        window.geometry(f"+{max(x, 0)}+{max(y, 0)}")
