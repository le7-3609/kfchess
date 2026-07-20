"""Board coordinate definition.

Owns: board coordinates (row, col) as a NamedTuple.
Must not own: pixels, clicks, rendering, movement rules, or timing.
"""

from typing import NamedTuple


class Position(NamedTuple):
    """A (row, col) board coordinate."""

    row: int
    col: int
