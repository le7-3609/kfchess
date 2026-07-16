"""Game-state model — mutable runtime state of a Kung Fu Chess game.

Owns: clock, selection, cooldowns, en-passant targets, game-over flag.
Active motions are owned by RealTimeArbiter, not GameState — see
realtime/arbiter.py.
Must not own: pixels, clicks, rendering, script parsing, movement rules.
"""

from dataclasses import dataclass, field
from typing import Generic, List, Optional, TypeVar

from kungfu_chess.errors import ResultAccessError
from kungfu_chess.model.position import Position
from kungfu_chess.model.piece import PieceInterface


T = TypeVar('T')
E = TypeVar('E')


class Result(Generic[T, E]):
    """A clean value-returning structure for success/failure handling."""

    def __init__(self, is_ok: bool, value: Optional[T] = None, error: Optional[E] = None) -> None:
        self.is_ok = is_ok
        self._value = value
        self._error = error

    @property
    def value(self) -> T:
        if not self.is_ok:
            raise ResultAccessError(f"Cannot retrieve value from a failed Result: {self._error}")
        return self._value  # type: ignore[return-value]

    @property
    def error(self) -> E:
        if self.is_ok:
            raise ResultAccessError("Cannot retrieve error from a successful Result")
        return self._error  # type: ignore[return-value]

    @classmethod
    def ok(cls, value: T) -> 'Result[T, E]':
        return cls(is_ok=True, value=value)

    @classmethod
    def fail(cls, error: E) -> 'Result[T, E]':
        return cls(is_ok=False, error=error)


@dataclass
class Movement:
    """A piece in transit between two squares."""
    frm: Position
    to: Position
    piece: PieceInterface
    start_ms: int
    arrival_ms: int


@dataclass
class Cooldown:
    """A piece recovering after arriving at its destination."""
    piece: PieceInterface
    end_ms: int


@dataclass
class EnPassantTarget:
    """An en-passant capture opportunity."""
    pos: Position          # the square that can be captured *into*
    capture_pos: Position  # the square holding the capturable pawn
    expires_ms: int


@dataclass
class GameState:
    """Tracks mutable game state: clock, selected piece, cooldowns, etc.

    Active motions in transit are not stored here — RealTimeArbiter owns
    that collection privately. Use the arbiter's register_motion /
    has_active_motion / movements methods instead of a GameState field.
    """
    clock_ms: int = 0
    selected_pos: Optional[Position] = None
    active_cooldowns: List[Cooldown] = field(default_factory=list)
    en_passant_targets: List[EnPassantTarget] = field(default_factory=list)
    game_over: bool = False
    game_over_reason: Optional[str] = None
    winner: Optional[str] = None
    position_history: List[str] = field(default_factory=list)
    halfmove_clock: int = 0
