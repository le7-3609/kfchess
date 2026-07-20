"""Path checker — capture-legality helpers for the Rule Engine (Layer 3).

Sliding/blocking geometry now lives in the piece rule classes
(shared.rules.piece_rules.legal_destinations); this module answers two
narrower questions that stay outside those stateless piece rules because they
need board occupancy at a specific square (is_path_clear) or move-history
state the piece rules never see (can_land's en-passant branch).

Must not own: board mutation, animation, click interpretation, game-over state.
"""

from typing import List, Optional

from shared.config import consts
from shared.model.position import Position
from shared.model.board import BoardInterface
from shared.model.piece import PieceInterface
from shared.rules.piece_rules import MoveValidatorFactoryInterface


class PathCheckerInterface:
    """Board-aware validator for path-blocking and capture legality (abstract)."""

    def is_path_clear(self, board: BoardInterface, frm: Position, to: Position) -> bool:  # type: ignore[empty-body]
        raise NotImplementedError

    def can_land(
        self,
        board: BoardInterface,
        moving_piece: PieceInterface,
        frm: Position,
        to: Position,
        en_passant_targets: Optional[List[Position]] = None,
    ) -> bool:  # type: ignore[empty-body]
        raise NotImplementedError


class PathChecker(PathCheckerInterface):
    """Concrete board-aware checker for path-blocking and capture rules."""

    def __init__(
        self,
        move_validator_factory: MoveValidatorFactoryInterface,
        config: 'GameConfig',  # type: ignore[name-defined]
    ) -> None:
        self._move_validator_factory = move_validator_factory
        self._config = config

    def is_path_clear(
        self,
        board: BoardInterface,
        frm: Position,
        to: Position,
    ) -> bool:
        """Return True if *to* is a legal (blocking- and occupancy-aware) destination
        for whatever piece currently sits at *frm*.

        Returns True if *frm* has no visible piece on *board* — e.g. when called
        with a re-validation snapshot that excludes the moving piece itself
        (see RealTimeArbiter.get_effective_board(..., exclude_mov=...)); there is
        nothing to check against, so the caller's other checks decide.
        """
        piece = board.get_piece(frm)
        if piece is None:
            return True
        validator = self._move_validator_factory.get_validator(piece.piece_type)
        return to in validator.legal_destinations(board, piece)

    def can_land(
        self,
        board: BoardInterface,
        moving_piece: PieceInterface,
        frm: Position,
        to: Position,
        en_passant_targets: Optional[List[Position]] = None,
    ) -> bool:
        """Return True if *moving_piece* is allowed to land on *to*.

        - Never land on a friendly piece.
        - Pawn forward move: destination must be empty.
        - Pawn diagonal move: destination must have an enemy, or be a
          currently-valid en-passant target reached by a genuine one-square
          diagonal-forward step (this is the one case legal_destinations
          intentionally excludes, since en passant depends on move-history
          state a stateless piece rule never sees).
        """
        occupant = board.get_piece(to)
        if occupant is not None and occupant.color == moving_piece.color:
            return False
        if moving_piece.piece_type == consts.PIECE_PAWN:
            return self._pawn_can_land(moving_piece, frm, to, occupant, en_passant_targets)
        return True

    def _pawn_can_land(
        self,
        pawn: PieceInterface,
        frm: Position,
        to: Position,
        occupant: Optional[PieceInterface],
        en_passant_targets: Optional[List[Position]],
    ) -> bool:
        """Return True if *pawn* may land on *to*, given whatever *occupant* is there.

        A pawn captures only sideways and advances only into an empty square,
        which is the reverse of how every other piece moves.
        """
        col_diff = abs(to.col - frm.col)
        if col_diff == consts.PAWN_FORWARD_COL_DIFF:
            return occupant is None
        if col_diff != consts.PAWN_DIAGONAL_COL_DIFF:
            return False
        if occupant is not None:
            return True
        return self._is_en_passant_landing(pawn, frm, to, en_passant_targets)

    def _is_en_passant_landing(
        self,
        pawn: PieceInterface,
        frm: Position,
        to: Position,
        en_passant_targets: Optional[List[Position]],
    ) -> bool:
        """Return True if *pawn*'s diagonal step onto an empty *to* is a valid en passant."""
        if en_passant_targets is None or to not in en_passant_targets:
            return False
        player_config = self._config.get_player(pawn.color)
        return player_config is not None and (to.row - frm.row) == player_config.forward_direction
