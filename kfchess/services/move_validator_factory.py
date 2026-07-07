"""Factory that maps PieceType → MoveValidatorInterface (Factory pattern).

A single shared instance of each validator is held in a class-level mapping,
so validator objects are effectively singletons — they carry no state.
"""

from typing import Dict

from kfchess.models.piece import PieceType
from kfchess.services.interfaces import MoveValidatorFactoryInterface, MoveValidatorInterface
from kfchess.services.move_validators import (
    BishopMoveValidator,
    KingMoveValidator,
    KnightMoveValidator,
    PawnMoveValidator,
    QueenMoveValidator,
    RookMoveValidator,
)


class MoveValidatorFactory(MoveValidatorFactoryInterface):
    """Concrete factory: returns the correct validator for any PieceType."""

    # Shared, stateless instances — safe to reuse across calls.
    _validators: Dict[PieceType, MoveValidatorInterface] = {
        PieceType.KING:   KingMoveValidator(),
        PieceType.QUEEN:  QueenMoveValidator(),
        PieceType.ROOK:   RookMoveValidator(),
        PieceType.BISHOP: BishopMoveValidator(),
        PieceType.KNIGHT: KnightMoveValidator(),
        PieceType.PAWN:   PawnMoveValidator(),
    }

    def get_validator(self, piece_type: PieceType) -> MoveValidatorInterface:
        """Return the MoveValidatorInterface for *piece_type*.

        Raises KeyError if an unregistered PieceType is encountered — this
        acts as a compile-time safety net when new piece types are added.
        """
        return self._validators[piece_type]
