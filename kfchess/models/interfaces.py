from abc import ABC, abstractmethod
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from kfchess.models.board import Position

class PieceStateInterface(ABC):
    """Abstract interface representing the state of a chess piece."""
    @abstractmethod
    def can_select(self) -> bool:
        """Return True if the piece can be selected."""

    @abstractmethod
    def can_move(self) -> bool:
        """Return True if the piece can start a move."""

class PieceInterface(ABC):
    """Abstract interface for a game piece.
    Decouples the data representation from game logic, allowing future
    support for binary representation.
    """
    @property
    @abstractmethod
    def color(self) -> str:
        """Returns the piece's color identifier."""

    @property
    @abstractmethod
    def piece_type(self) -> str:
        """Returns the piece's type identifier."""

    @abstractmethod
    def transition_to_moving(self) -> None:
        """Transition the piece to moving state."""

    @abstractmethod
    def transition_to_jumping(self) -> None:
        """Transition the piece to jumping state."""

    @abstractmethod
    def transition_to_idle(self) -> None:
        """Transition the piece back to idle state."""

    @abstractmethod
    def transition_to_cooldown(self) -> None:
        """Transition the piece to cooldown state."""

    @abstractmethod
    def can_select(self) -> bool:
        """Query if the piece is selectable in its current state."""

    @abstractmethod
    def can_move(self) -> bool:
        """Query if the piece can start a movement in its current state."""

class BoardInterface(ABC):
    """Abstract interface for a game board.
    Decouples the storage representation (e.g., Array, Bitboard) from game logic.
    """
    @property
    @abstractmethod
    def rows(self) -> int:
        """Returns the number of rows on the board."""

    @property
    @abstractmethod
    def cols(self) -> int:
        """Returns the number of columns on the board."""

    @abstractmethod
    def is_valid_position(self, pos: 'Position') -> bool:
        """Return True if pos is within the board boundaries."""

    @abstractmethod
    def get_piece(self, pos: 'Position') -> Optional[PieceInterface]:
        """Return the piece at pos, or None if empty."""

    @abstractmethod
    def set_piece(self, pos: 'Position', piece: Optional[PieceInterface]) -> None:
        """Place or remove a piece at pos."""
