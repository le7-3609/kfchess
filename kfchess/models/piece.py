from typing import Optional

from kfchess.models.interfaces import PieceInterface, PieceStateInterface


class IdleState(PieceStateInterface):
    """State of a piece that is static on the board."""

    def can_select(self) -> bool:
        return True

    def can_move(self) -> bool:
        return True


class MovingState(PieceStateInterface):
    """State of a piece that is currently in motion."""

    def can_select(self) -> bool:
        return False

    def can_move(self) -> bool:
        return False


class JumpingState(PieceStateInterface):
    """State of a piece that is currently jumping (airborne)."""

    def can_select(self) -> bool:
        return False

    def can_move(self) -> bool:
        return False


class CooldownState(PieceStateInterface):
    """State of a piece that is in cooldown after arriving."""

    def can_select(self) -> bool:
        return False

    def can_move(self) -> bool:
        return False


class TextPiece(PieceInterface):
    """Text-based implementation of a chess piece."""
    
    def __init__(self, color: str, piece_type: str) -> None:
        self._color = color
        self._piece_type = piece_type
        self._state: PieceStateInterface = IdleState()

    @property
    def color(self) -> str:
        return self._color

    @property
    def piece_type(self) -> str:
        return self._piece_type

    @piece_type.setter
    def piece_type(self, value: str) -> None:
        self._piece_type = value

    def transition_to_moving(self) -> None:
        """Transition the piece to MovingState."""
        self._state = MovingState()

    def transition_to_jumping(self) -> None:
        """Transition the piece to JumpingState."""
        self._state = JumpingState()

    def transition_to_idle(self) -> None:
        """Transition the piece back to IdleState."""
        self._state = IdleState()

    def transition_to_cooldown(self) -> None:
        """Transition the piece to CooldownState."""
        self._state = CooldownState()

    def can_select(self) -> bool:
        """Query if the piece is selectable in its current state."""
        return self._state.can_select()

    def can_move(self) -> bool:
        """Query if the piece can start a movement in its current state."""
        return self._state.can_move()

    def __str__(self) -> str:
        return f"{self._color}{self._piece_type}"

    def __repr__(self) -> str:
        return f"TextPiece({self._color}, {self._piece_type})"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PieceInterface):
            return False
        return self.color == other.color and self.piece_type == other.piece_type


class PieceFactory:
    """Factory to create pieces from string tokens."""
    
    @staticmethod
    def from_string(token: str) -> Optional[PieceInterface]:
        if len(token) != 2:
            return None
        color_char, piece_char = token[0], token[1]
        
        if color_char not in ('w', 'b'):
            return None
        if piece_char not in ('K', 'Q', 'R', 'B', 'N', 'P'):
            return None
            
        return TextPiece(color_char, piece_char)
