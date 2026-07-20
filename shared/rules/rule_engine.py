"""Rule engine — re-exports plus the common-route legality gate (Layer 3).

Concrete implementations live in:
  - rules/path_checker.py      (PathCheckerInterface, PathChecker)
  - rules/threat_validator.py  (ThreatValidator)
  - rules/endgame_validator.py (EndgameValidator, serialize_board_state)
  - rules/castling_validator.py (CastlingValidator, CastlingDestinations)

RuleEngine answers one question: given a source cell and a destination
cell, is this command legal now? It is read-only with respect to Board —
it inspects state and returns a MoveValidation, but never moves pieces,
removes captures, starts motions, or updates game state.

The common route (RuleEngine.validate_move) does not implement check,
pins, checkmate, castling, en passant, or promotion. The win condition
is king capture. Game-over is handled by GameEngine, not RuleEngine.
"""

from dataclasses import dataclass
from typing import List, Optional

from shared.model.position import Position
from shared.model.board import BoardInterface
from shared.rules.path_checker import PathCheckerInterface, PathChecker
from shared.rules.piece_rules import MoveValidatorFactoryInterface
from shared.rules.threat_validator import ThreatValidator
from shared.rules.endgame_validator import EndgameValidator, serialize_board_state
from shared.rules.castling_validator import CastlingValidator, CastlingDestinations

__all__ = [
    "MoveValidation",
    "RuleEngine",
    "PathCheckerInterface",
    "PathChecker",
    "ThreatValidator",
    "EndgameValidator",
    "serialize_board_state",
    "CastlingValidator",
    "CastlingDestinations",
]


@dataclass(frozen=True)
class MoveValidation:
    """Result of a RuleEngine legality check. *reason* is always present:
    "ok" when valid, otherwise a stable machine-readable code.
    """

    is_valid: bool
    reason: str


class RuleEngine:
    """Read-only common-route legality gate.

    Rejects moves outside the board, from empty cells, or onto a
    friendly-occupied destination, then defers to the relevant movement
    rule for destination legality — including en passant, when the caller
    supplies the currently-valid target squares. Does not implement check,
    pins, checkmate, castling, or promotion — those live in ThreatValidator,
    CastlingValidator, and EndgameValidator.
    """

    def __init__(self, move_validator_factory: MoveValidatorFactoryInterface) -> None:
        self._move_validator_factory = move_validator_factory

    def validate_move(
        self,
        board: BoardInterface,
        frm: Position,
        to: Position,
        en_passant_targets: Optional[List[Position]] = None,
    ) -> MoveValidation:
        """Return whether moving from *frm* to *to* is legal on *board* right now.

        *en_passant_targets*, when given, lets a pawn's currently-valid
        en-passant square pass this check too — RuleEngine itself holds no
        move-history state; it just forwards whatever the caller supplies.
        """
        if not board.is_valid_position(frm) or not board.is_valid_position(to):
            return MoveValidation(False, "outside_board")

        piece = board.get_piece(frm)
        if piece is None:
            return MoveValidation(False, "empty_source")

        destination = board.get_piece(to)
        if destination is not None and destination.color == piece.color:
            return MoveValidation(False, "friendly_destination")

        validator = self._move_validator_factory.get_validator(piece.piece_type)
        if to not in validator.legal_destinations(board, piece, en_passant_targets):
            return MoveValidation(False, "illegal_piece_move")

        return MoveValidation(True, "ok")
