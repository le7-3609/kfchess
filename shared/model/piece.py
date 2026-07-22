"""Piece model — piece identity and lifecycle state.

Owns: piece identity (unique id, color, type), piece lifecycle state
(idle/moving/jumping/cooldown).
Must not own: pixels, clicks, rendering, script parsing, movement rules, or timing.
"""

from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID, uuid4

from shared.config import consts

# How many hex digits of the piece UUID __repr__ shows — enough to tell twins
# apart in debug output without flooding it.
_REPR_ID_PREFIX_LENGTH = 8


class PieceStateInterface(ABC):
    """Abstract interface representing the lifecycle state of a chess piece."""

    @abstractmethod
    def can_select(self) -> bool:
        """Return True if the piece can be selected in this state."""

    @abstractmethod
    def can_move(self) -> bool:
        """Return True if the piece can start a move in this state."""


class IdleState(PieceStateInterface):
    """The piece is static on the board — fully available."""

    def can_select(self) -> bool:
        return True

    def can_move(self) -> bool:
        return True


class MovingState(PieceStateInterface):
    """The piece is currently sliding between squares."""

    def can_select(self) -> bool:
        return False

    def can_move(self) -> bool:
        return False


class JumpingState(PieceStateInterface):
    """The piece is airborne (e.g. Knight mid-jump)."""

    def can_select(self) -> bool:
        return False

    def can_move(self) -> bool:
        return False


class CooldownState(PieceStateInterface):
    """The piece has just arrived and is recovering."""

    def can_select(self) -> bool:
        return False

    def can_move(self) -> bool:
        return False


class PieceInterface(ABC):
    """Abstract interface for a game piece.

    Decouples the data representation from game logic, allowing future
    support for alternative representations (e.g. bitboard pieces).

    Pieces are identified by *piece_id*, never by (color, type): the board
    holds many interchangeable-looking pieces, but the arbiter, collision
    resolver and cooldown lists all track one specific piece in flight.
    """

    @property
    @abstractmethod
    def piece_id(self) -> UUID:
        """Returns the identity that distinguishes this piece from its twins."""

    @property
    @abstractmethod
    def color(self) -> str:
        """Returns the piece's color identifier ('w' or 'b')."""

    @property
    @abstractmethod
    def piece_type(self) -> str:
        """Returns the piece's type identifier ('K', 'Q', 'R', 'B', 'N', 'P')."""

    @property
    @abstractmethod
    def has_moved(self) -> bool:
        """Returns True if the piece has ever left its starting square."""

    @abstractmethod
    def transition_to_moving(self) -> None:
        """Transition the piece to MovingState."""

    @abstractmethod
    def transition_to_jumping(self) -> None:
        """Transition the piece to JumpingState (airborne)."""

    @abstractmethod
    def transition_to_idle(self) -> None:
        """Transition the piece back to IdleState."""

    @abstractmethod
    def transition_to_cooldown(self) -> None:
        """Transition the piece to CooldownState."""

    @abstractmethod
    def can_select(self) -> bool:
        """Query if the piece is selectable in its current state."""

    @abstractmethod
    def can_move(self) -> bool:
        """Query if the piece can start a movement in its current state."""


class TextPiece(PieceInterface):
    """Text-based implementation of a chess piece."""

    def __init__(self, color: str, piece_type: str, has_moved: bool = False) -> None:
        self._piece_id = uuid4()
        self._color = color
        self._piece_type = piece_type
        self._state: PieceStateInterface = IdleState()
        self._has_moved = has_moved

    @property
    def piece_id(self) -> UUID:
        return self._piece_id

    @property
    def color(self) -> str:
        return self._color

    @property
    def piece_type(self) -> str:
        return self._piece_type

    @property
    def has_moved(self) -> bool:
        return self._has_moved

    def transition_to_moving(self) -> None:
        """Transition the piece to MovingState and mark it as having moved."""
        self._has_moved = True
        self._state = MovingState()

    def transition_to_jumping(self) -> None:
        self._state = JumpingState()

    def transition_to_idle(self) -> None:
        self._state = IdleState()

    def transition_to_cooldown(self) -> None:
        self._state = CooldownState()

    def can_select(self) -> bool:
        return self._state.can_select()

    def can_move(self) -> bool:
        return self._state.can_move()

    def __str__(self) -> str:
        return f"{self._color}{self._piece_type}"

    def __repr__(self) -> str:
        return f"TextPiece({self._color}, {self._piece_type}, {str(self._piece_id)[:_REPR_ID_PREFIX_LENGTH]})"

    def __eq__(self, other: object) -> bool:
        """Compare identity, not appearance.

        Two separately created white rooks are distinct pieces even though
        they look identical. Value equality here used to make the arbiter
        report a piece as in-flight because its twin was moving, and made
        list.remove() drop whichever twin's Movement/Cooldown came first.
        """
        if not isinstance(other, PieceInterface):
            return NotImplemented
        return self.piece_id == other.piece_id

    def __hash__(self) -> int:
        return hash(self._piece_id)


class PieceFactory:
    """Factory to create TextPiece instances from string tokens like 'wK'."""

    VALID_COLORS = frozenset(consts.ALL_COLORS)
    VALID_TYPES = frozenset(consts.ALL_PIECE_TYPES)

    @staticmethod
    def from_string(token: str) -> Optional[PieceInterface]:
        """Return a TextPiece for *token*, or None if the token is invalid."""
        if len(token) != consts.PIECE_TOKEN_LENGTH:
            return None
        color_char = token[consts.TOKEN_COLOR_INDEX]
        piece_char = token[consts.TOKEN_PIECE_TYPE_INDEX]
        if color_char not in PieceFactory.VALID_COLORS:
            return None
        if piece_char not in PieceFactory.VALID_TYPES:
            return None
        return TextPiece(color_char, piece_char)
