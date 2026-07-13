"""Game snapshot — the Renderer/View layer's sole read model (Layer 6 boundary).

Owns: an immutable, point-in-time view of the board and game state, built for
      rendering.
Must not own: game rules, board mutation, input parsing, or timing advancement.
"""

import types
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from kungfu_chess.model.position import Position


@dataclass(frozen=True)
class PieceSnapshot:
    """Read-only view of a single piece, as needed to render it."""

    color: str
    piece_type: str
    has_moved: bool
    can_select: bool
    can_move: bool


@dataclass(frozen=True)
class MovementSnapshot:
    """Read-only view of a piece in transit between two squares."""

    frm: Position
    to: Position
    piece: PieceSnapshot
    start_ms: int
    arrival_ms: int


@dataclass(frozen=True)
class GameSnapshot:
    """Immutable, point-in-time view of the game for rendering.

    Built once from the mutable Board/GameState and handed to the Renderer,
    which may only read from it — never from BoardInterface or GameState
    directly.
    """

    rows: int
    cols: int
    pieces: Mapping[Position, PieceSnapshot]
    selected_pos: Optional[Position]
    active_movements: Tuple[MovementSnapshot, ...]
    cooldown_positions: Tuple[Position, ...]
    clock_ms: int
    game_over: bool
    game_over_reason: Optional[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pieces", types.MappingProxyType(dict(self.pieces)))
        object.__setattr__(self, "active_movements", tuple(self.active_movements))
        object.__setattr__(self, "cooldown_positions", tuple(self.cooldown_positions))

    def piece_at(self, pos: Position) -> Optional[PieceSnapshot]:
        """Return the piece at *pos*, or None if the square is empty."""
        return self.pieces.get(pos)
