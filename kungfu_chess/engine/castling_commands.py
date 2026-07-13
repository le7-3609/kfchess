"""Castling orchestration (Layer 5).

Owns: turning a king+rook "friendly click" into a legality check
(delegated to rules.CastlingValidator) and, if legal, enqueuing the paired
King/Rook movements.
Must not own: castling legality itself (that's CastlingValidator's job).
"""

from typing import Callable

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.model.game_state import GameState, Movement
from kungfu_chess.rules.rule_engine import CastlingValidator
from kungfu_chess.engine.engine_interfaces import GameStateRepositoryInterface
from kungfu_chess.realtime.arbiter_interfaces import RealTimeArbiterInterface


class CastlingCommands:
    """Attempts and executes castling moves."""

    def __init__(
        self,
        arbiter: RealTimeArbiterInterface,
        castling_validator: CastlingValidator,
        state_repo: GameStateRepositoryInterface,
        resolve_pending: Callable[[], None],
    ) -> None:
        self._arbiter = arbiter
        self._castling_validator = castling_validator
        self._state_repo = state_repo
        self._resolve_pending = resolve_pending

    def is_castle_attempt(
        self,
        king_piece: PieceInterface,
        rook_piece: PieceInterface,
        king_pos: Position,
        rook_pos: Position,
    ) -> bool:
        return self._castling_validator.is_castle_attempt(king_piece, rook_piece, king_pos, rook_pos)

    def try_castle(
        self,
        state: GameState,
        board: BoardInterface,
        king_pos: Position,
        rook_pos: Position,
        king_piece,
        rook_piece,
    ) -> bool:
        eff_board = self._arbiter.get_effective_board(board, state, state.clock_ms)
        destinations = self._castling_validator.get_legal_castle(eff_board, king_pos, rook_pos, king_piece)
        if destinations is None:
            return False

        self._execute_castle(
            state, king_pos, rook_pos, destinations.king_dest, destinations.rook_dest, king_piece, rook_piece
        )
        return True

    def _execute_castle(
        self,
        state: GameState,
        king_pos: Position,
        rook_pos: Position,
        king_dest: Position,
        rook_dest: Position,
        king_piece,
        rook_piece,
    ) -> None:
        king_arrival = self._arbiter.calculate_arrival(king_pos, king_dest, king_piece, state.clock_ms)
        rook_arrival = king_arrival

        king_mov = Movement(frm=king_pos, to=king_dest, piece=king_piece, start_ms=state.clock_ms, arrival_ms=king_arrival)
        rook_mov = Movement(frm=rook_pos, to=rook_dest, piece=rook_piece, start_ms=state.clock_ms, arrival_ms=rook_arrival)

        state.active_movements.extend([rook_mov, king_mov])
        king_piece.transition_to_moving()
        rook_piece.transition_to_moving()
        state.selected_pos = None
        self._state_repo.save_state(state)
        self._resolve_pending()
