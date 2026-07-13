"""RealTimeArbiter contract (Layer 4).

Extracted from arbiter.py so collision_resolver.py and arrival_resolver.py can
depend on the interface without importing the concrete arbiter (would cycle).
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.model.game_state import GameState, Movement


class RealTimeArbiterInterface(ABC):
    """Abstract contract for the real-time arbiter.

    Owns the collection of active Motion (Movement) objects — GameState
    does not store them. Other layers register/query motions through
    register_motion / has_active_motion / movements / remove_motion
    rather than reaching into a shared list themselves.
    """

    @abstractmethod
    def register_motion(self, mov: Movement) -> None:
        """Add *mov* to the set of active motions."""

    @abstractmethod
    def remove_motion(self, mov: Movement) -> None:
        """Remove *mov* from the set of active motions, if present."""

    @abstractmethod
    def movements(self) -> List[Movement]:
        """Return a snapshot list of currently active motions."""

    @abstractmethod
    def has_active_motion(self, piece: Optional[PieceInterface] = None) -> bool:
        """Return whether a motion is active.

        With no argument, returns whether any motion is active at all.
        With *piece*, returns whether that specific piece has a motion
        in flight — this is the check GameEngine/command processors use
        to enforce the one-active-motion-per-piece policy.
        """

    @abstractmethod
    def has_active_motion_for_color(self, color: str) -> bool:
        """Return whether any piece of *color* currently has a motion in flight."""

    @abstractmethod
    def calculate_arrival(self, frm: Position, to: Position, piece: PieceInterface, start_ms: int) -> int:
        """Return the arrival timestamp in milliseconds."""

    @abstractmethod
    def get_position_at(self, mov: Movement, t: int) -> Position:
        """Return the interpolated board position of *mov* at time *t*."""

    @abstractmethod
    def resolve_movements(self, board: BoardInterface, state: GameState, current_ms: int) -> None:
        """Update *board* with any pieces that have finished transit by *current_ms*."""

    @abstractmethod
    def get_effective_board(
        self,
        board: BoardInterface,
        state: GameState,
        t: int,
        exclude_mov: Optional[Movement] = None,
    ) -> BoardInterface:
        """Return a BoardInterface showing all piece locations at time *t*.

        If *exclude_mov* is given, that movement is left out of the projection
        (used when a movement is checking its own path/landing so it doesn't
        appear to block itself).
        """

    @abstractmethod
    def get_valid_en_passant_positions(
        self,
        board: BoardInterface,
        state: GameState,
        color: str,
        t: int
    ) -> List[Position]:
        """Return a list of valid en-passant target positions for a player of *color* at time *t*."""
