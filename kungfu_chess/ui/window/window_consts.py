"""Shared window-layer constants (UI window/controls layer).

Owns: the geometry and tick-rate defaults common to every tkinter window, so
the live game and the replay stay visually identical.
Must not own: game rules, timing semantics, or anything a window does not draw.
"""

TICK_MS = 16
BOARD_SIZE = 640
