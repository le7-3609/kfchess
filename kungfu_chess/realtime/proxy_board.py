"""ProxyBoard — efficient effective-board snapshot (Layer 4).

A read-mostly board view that dynamically overlays active motions, used by
the RealTimeArbiter to answer "where is everything right now" without
copying the full board at every simulation step.
"""

from typing import List, Optional

from kungfu_chess.errors import EmptyCellError, InvalidPositionError, OccupiedCellError
from kungfu_chess.model.position import Position
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.piece import PieceInterface
from kungfu_chess.model.game_state import Movement


class ProxyBoard(BoardInterface):
    """A read-mostly board view that dynamically overlays active motions.

    Rather than copying the full board at every simulation step, ProxyBoard
    computes piece positions on the fly, making it O(active_motions) per lookup.
    """

    def __init__(
        self,
        board: BoardInterface,
        active_movements: List[Movement],
        t: int,
        get_position_fn,
        exclude_mov: Optional[Movement] = None,
    ) -> None:
        self._board = board
        self._rows = board.rows
        self._cols = board.cols
        self._exclude_mov = exclude_mov
        self._exclude_piece = exclude_mov.piece if exclude_mov is not None else None

        self._moving_at_pos = {}
        self._moving_piece_ids: set = set()
        for mov in active_movements:
            if mov == exclude_mov:
                continue
            pos_at_t = get_position_fn(mov, t)
            self._moving_at_pos[pos_at_t] = mov.piece
            self._moving_piece_ids.add(id(mov.piece))

        self._overrides: dict = {}

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def cols(self) -> int:
        return self._cols

    def is_valid_position(self, pos: Position) -> bool:
        return self._board.is_valid_position(pos)

    def get_piece(self, pos: Position) -> Optional[PieceInterface]:
        if not self.is_valid_position(pos):
            raise InvalidPositionError(pos)
        if pos in self._overrides:
            return self._overrides[pos]
        if pos in self._moving_at_pos:
            return self._moving_at_pos[pos]
        piece = self._board.get_piece(pos)
        if piece is not None:
            if self._exclude_piece is not None and piece is self._exclude_piece:
                return None
            if id(piece) in self._moving_piece_ids:
                return None
            return piece
        return None

    def find_position(self, piece: PieceInterface) -> Optional[Position]:
        for r in range(self._rows):
            for c in range(self._cols):
                pos = Position(r, c)
                if self.get_piece(pos) is piece:
                    return pos
        return None

    def set_piece(self, pos: Position, piece: Optional[PieceInterface]) -> None:
        if not self.is_valid_position(pos):
            raise InvalidPositionError(pos)
        self._overrides[pos] = piece

    def add_piece(self, pos: Position, piece: PieceInterface) -> None:
        if not self.is_valid_position(pos):
            raise InvalidPositionError(pos)
        if self.get_piece(pos) is not None:
            raise OccupiedCellError(pos)
        self._overrides[pos] = piece

    def remove_piece(self, pos: Position) -> Optional[PieceInterface]:
        if not self.is_valid_position(pos):
            raise InvalidPositionError(pos)
        piece = self.get_piece(pos)
        self._overrides[pos] = None
        return piece

    def move_piece(self, frm: Position, to: Position) -> Optional[PieceInterface]:
        if not self.is_valid_position(frm) or not self.is_valid_position(to):
            raise InvalidPositionError(frm if not self.is_valid_position(frm) else to)
        piece = self.get_piece(frm)
        if piece is None:
            raise EmptyCellError(frm)
        captured = self.get_piece(to)
        self._overrides[to] = piece
        self._overrides[frm] = None
        return captured
