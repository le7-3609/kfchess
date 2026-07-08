"""Factory that maps piece_type (str) → MoveValidatorInterface (Factory pattern).

A single shared instance of each validator is held in a class-level mapping,
so validator objects are effectively singletons — they carry no state.
"""

from typing import Dict

from kfchess.services.interfaces import MoveValidatorFactoryInterface, MoveValidatorInterface


class MoveValidatorFactory(MoveValidatorFactoryInterface):
    """Concrete factory: returns the correct validator for any piece type."""

    def __init__(self, validators: Dict[str, MoveValidatorInterface]) -> None:
        self._validators = validators

    def get_validator(self, piece_type: str) -> MoveValidatorInterface:
        """Return the MoveValidatorInterface for *piece_type*.

        Raises KeyError if an unregistered piece type is encountered — this
        acts as a compile-time safety net when new piece types are added.
        """
        return self._validators[piece_type]
