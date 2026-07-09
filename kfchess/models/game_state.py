from dataclasses import dataclass, field
from typing import List, Optional

from kfchess.models.board import Position
from kfchess.models.interfaces import PieceInterface


@dataclass
class Movement:
    frm: Position
    to: Position
    piece: PieceInterface
    start_ms: int
    arrival_ms: int


@dataclass
class Cooldown:
    piece: PieceInterface
    end_ms: int


@dataclass
class EnPassantTarget:
    pos: Position
    capture_pos: Position
    expires_ms: int


@dataclass
class GameState:
    """Tracks mutable game state: the clock, the selected piece, and movements in transit."""
    clock_ms: int = 0
    selected_pos: Optional[Position] = None
    active_movements: List[Movement] = field(default_factory=list)
    active_cooldowns: List[Cooldown] = field(default_factory=list)
    en_passant_targets: List[EnPassantTarget] = field(default_factory=list)
    game_over: bool = False
    game_over_reason: Optional[str] = None
    position_history: List[str] = field(default_factory=list)
    halfmove_clock: int = 0

