"""Per-piece movement rules (Layer 2).

Each concrete validator encodes both the geometric movement shape *and* the
blocking/capture semantics for one piece type: legal_destinations(board, piece)
walks the board directly, so sliding pieces stop before a friendly blocker and
include (but don't pass through) an enemy blocker. Validators are stateless —
they read board/piece data given to them and never store selection, timing,
or game-over state, and never mutate the board or piece.

Castling is intentionally not modeled here: it depends on move-history state
(has the king/rook moved) that these piece rules never see, and stays owned
by the Rule Engine layer (CastlingValidator). En passant is a narrower case —
the *destination square* is ordinary pawn-diagonal geometry, so PawnMoveValidator
computes it too, but only when told which squares are currently valid
en-passant targets; the piece rule still stores no history itself, it just
reads whatever *en_passant_targets* the caller passes in for this one call.

Also contains:
  - MoveValidatorFactory  — maps piece_type str -> MoveValidatorInterface
  - PromotionStrategyInterface / StandardPawnPromotion
"""

from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional, Set, Tuple

from kungfu_chess.config import consts
from kungfu_chess.errors import MissingValidatorError
from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface, TextPiece


# ---------------------------------------------------------------------------
# MoveValidator interface
# ---------------------------------------------------------------------------

class MoveValidatorInterface(ABC):
    """Computes the legal destination squares for one piece type."""

    @abstractmethod
    def legal_destinations(
        self,
        board: BoardInterface,
        piece: PieceInterface,
        en_passant_targets: Optional[List[Position]] = None,
    ) -> Set[Position]:
        """Return every square *piece* may legally move to on *board*.

        Friendly-occupied squares are excluded. Enemy-occupied squares may be
        included (a capture), but for sliding pieces they terminate the slide.
        *en_passant_targets*, when given, are empty squares a pawn may also
        capture into (ignored by non-pawn validators). Does not mutate
        *board* or *piece*.
        """


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _step_destinations(
    board: BoardInterface, piece: PieceInterface, offsets: Iterable[Tuple[int, int]]
) -> Set[Position]:
    """Legal destinations for a single-step (non-sliding) piece: King, Knight."""
    frm = board.find_position(piece)
    destinations: Set[Position] = set()
    if frm is None:
        return destinations
    for dr, dc in offsets:
        pos = Position(frm.row + dr, frm.col + dc)
        if board.is_valid_position(pos):
            occupant = board.get_piece(pos)
            if occupant is None or occupant.color != piece.color:
                destinations.add(pos)
    return destinations


def _sliding_destinations(
    board: BoardInterface, piece: PieceInterface, directions: Iterable[Tuple[int, int]]
) -> Set[Position]:
    """Legal destinations for a sliding piece: Rook, Bishop (and Queen via both)."""
    frm = board.find_position(piece)
    destinations: Set[Position] = set()
    if frm is None:
        return destinations
    for dr, dc in directions:
        pos = Position(frm.row + dr, frm.col + dc)
        while board.is_valid_position(pos):
            occupant = board.get_piece(pos)
            if occupant is None:
                destinations.add(pos)
            else:
                if occupant.color != piece.color:
                    destinations.add(pos)
                break
            pos = Position(pos.row + dr, pos.col + dc)
    return destinations


# ---------------------------------------------------------------------------
# Concrete piece validators
# ---------------------------------------------------------------------------

class KingMoveValidator(MoveValidatorInterface):
    """King may move exactly one square in any direction."""

    _OFFSETS = tuple((dr, dc) for dr in (-1, 0, 1) for dc in (-1, 0, 1) if (dr, dc) != (0, 0))

    def legal_destinations(
        self, board: BoardInterface, piece: PieceInterface, en_passant_targets: Optional[List[Position]] = None
    ) -> Set[Position]:
        return _step_destinations(board, piece, self._OFFSETS)


class RookMoveValidator(MoveValidatorInterface):
    """Rook slides any number of squares along a rank or file, until blocked."""

    _DIRECTIONS = ((-1, 0), (1, 0), (0, -1), (0, 1))

    def legal_destinations(
        self, board: BoardInterface, piece: PieceInterface, en_passant_targets: Optional[List[Position]] = None
    ) -> Set[Position]:
        return _sliding_destinations(board, piece, self._DIRECTIONS)


class BishopMoveValidator(MoveValidatorInterface):
    """Bishop slides any number of squares diagonally, until blocked."""

    _DIRECTIONS = ((-1, -1), (-1, 1), (1, -1), (1, 1))

    def legal_destinations(
        self, board: BoardInterface, piece: PieceInterface, en_passant_targets: Optional[List[Position]] = None
    ) -> Set[Position]:
        return _sliding_destinations(board, piece, self._DIRECTIONS)


class QueenMoveValidator(MoveValidatorInterface):
    """Queen combines Rook and Bishop movement."""

    def __init__(self) -> None:
        self._rook = RookMoveValidator()
        self._bishop = BishopMoveValidator()

    def legal_destinations(
        self, board: BoardInterface, piece: PieceInterface, en_passant_targets: Optional[List[Position]] = None
    ) -> Set[Position]:
        return self._rook.legal_destinations(board, piece) | self._bishop.legal_destinations(board, piece)


class KnightMoveValidator(MoveValidatorInterface):
    """Knight moves in an L-shape, ignoring blockers along the way."""

    _OFFSETS = ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1))

    def legal_destinations(
        self, board: BoardInterface, piece: PieceInterface, en_passant_targets: Optional[List[Position]] = None
    ) -> Set[Position]:
        return _step_destinations(board, piece, self._OFFSETS)


class PawnMoveValidator(MoveValidatorInterface):
    """Pawn moves 1 square forward (or 2 from its start row) into an empty square,
    or 1 square diagonally forward onto an enemy piece.

    Requires a GameConfig to determine forward direction and start rows per player.
    En passant destinations are not inferred from board state alone — callers
    pass the currently valid en-passant squares via *en_passant_targets*.
    """

    def __init__(self, config: 'GameConfig') -> None:  # type: ignore[name-defined]
        self._config = config

    def legal_destinations(
        self, board: BoardInterface, piece: PieceInterface, en_passant_targets: Optional[List[Position]] = None
    ) -> Set[Position]:
        frm = board.find_position(piece)
        player_config = self._config.get_player(piece.color)
        destinations: Set[Position] = set()
        if frm is None or player_config is None:
            return destinations

        direction = player_config.forward_direction

        one_step = Position(frm.row + direction, frm.col)
        if board.is_valid_position(one_step) and board.get_piece(one_step) is None:
            destinations.add(one_step)
            if frm.row in player_config.pawn_start_rows:
                two_step = Position(frm.row + direction * 2, frm.col)
                if board.is_valid_position(two_step) and board.get_piece(two_step) is None:
                    destinations.add(two_step)

        for dc in (-1, 1):
            diag = Position(frm.row + direction, frm.col + dc)
            if board.is_valid_position(diag):
                occupant = board.get_piece(diag)
                if occupant is not None and occupant.color != piece.color:
                    destinations.add(diag)
                elif occupant is None and en_passant_targets is not None and diag in en_passant_targets:
                    destinations.add(diag)

        return destinations


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class MoveValidatorFactoryInterface(ABC):
    """Creates (or retrieves) the correct MoveValidatorInterface for a piece."""

    @abstractmethod
    def get_validator(self, piece_type: str) -> MoveValidatorInterface:
        """Return the MoveValidatorInterface instance for *piece_type*."""


class MoveValidatorFactory(MoveValidatorFactoryInterface):
    """Simple dict-based factory (Strategy + Factory pattern)."""

    def __init__(self, validators: Dict[str, MoveValidatorInterface]) -> None:
        self._validators = validators

    def get_validator(self, piece_type: str) -> MoveValidatorInterface:
        validator = self._validators.get(piece_type)
        if validator is None:
            raise MissingValidatorError(piece_type)
        return validator


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------

class PromotionStrategyInterface(ABC):
    """Abstract interface for piece promotion rules."""

    @abstractmethod
    def evaluate_promotion(
        self, piece: PieceInterface, to_pos: Position, config: 'GameConfig'  # type: ignore[name-defined]
    ) -> Optional[PieceInterface]:
        """Return a new piece to replace *piece* after it moves to *to_pos*, or None.

        Pieces are immutable value objects: promotion never mutates *piece* in
        place, it constructs a fresh PieceInterface for the caller to install.
        """


class StandardPawnPromotion(PromotionStrategyInterface):
    """Auto-promotes a pawn to queen when it reaches the opposite back rank."""

    def evaluate_promotion(
        self, piece: PieceInterface, to_pos: Position, config: 'GameConfig'  # type: ignore[name-defined]
    ) -> Optional[PieceInterface]:
        if piece.piece_type != "P":
            return None
        player_config = config.get_player(piece.color)
        if player_config is None:
            return None
        if to_pos.row != player_config.promotion_rank:
            return None
        return TextPiece(piece.color, consts.DEFAULT_PROMOTION_PIECE, has_moved=True)
