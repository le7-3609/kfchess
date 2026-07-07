from kfchess.models.board import Board, Position
from kfchess.models.game_state import GameState, Movement
from kfchess.models.piece import Piece
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.interfaces import (
    MovementDurationInterface,
    MovementManagerInterface,
)


class InstantMovementDuration(MovementDurationInterface):
    """Strategy that makes all movements instant (0 duration)."""

    def calculate_duration(self, frm: Position, to: Position, piece: Piece) -> int:
        return 0


class ChebyshevDistanceDuration(MovementDurationInterface):
    """Strategy that calculates duration based on Chebyshev distance."""

    def __init__(self, ms_per_square: int = 1000) -> None:
        self._ms_per_square = ms_per_square

    def calculate_duration(self, frm: Position, to: Position, piece: Piece) -> int:
        dist = max(abs(to.row - frm.row), abs(to.col - frm.col))
        return dist * self._ms_per_square


class MovementManager(MovementManagerInterface):
    """Manages active movements, calculates arrival times, and resolves arrivals."""

    def __init__(
        self,
        duration_strategy: MovementDurationInterface,
        move_event_publisher: MoveEventPublisher,
    ) -> None:
        self._duration_strategy = duration_strategy
        self._move_event_publisher = move_event_publisher

    def calculate_arrival(self, frm: Position, to: Position, piece: Piece, start_ms: int) -> int:
        duration = self._duration_strategy.calculate_duration(frm, to, piece)
        return start_ms + duration

    def resolve_movements(self, board: Board, state: GameState, current_ms: int) -> None:
        arrived = []
        remaining = []

        for mov in state.active_movements:
            if mov.arrival_ms <= current_ms:
                arrived.append(mov)
            else:
                remaining.append(mov)

        # Sort by arrival time to resolve in chronological order.
        arrived.sort(key=lambda m: m.arrival_ms)

        for mov in arrived:
            current_piece = board.get_piece(mov.frm)
            if current_piece == mov.piece:
                board.set_piece(mov.frm, None)
                board.set_piece(mov.to, mov.piece)
                mov.piece.transition_to_idle()
                self._move_event_publisher.publish(mov.piece, mov.frm, mov.to)
            else:
                mov.piece.transition_to_idle()

        state.active_movements = remaining
