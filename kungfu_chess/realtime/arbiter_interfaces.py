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
    """Abstract contract for the real-time arbiter."""

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
