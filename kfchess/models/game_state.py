from dataclasses import dataclass
from typing import Optional

from kfchess.models.board import Position


@dataclass
class GameState:
    """Tracks mutable game state: the clock and the currently selected piece."""
    clock_ms: int = 0
    selected_pos: Optional[Position] = None
