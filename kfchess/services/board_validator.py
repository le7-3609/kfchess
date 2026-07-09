from typing import List

from kfchess.models.board import Position
from kfchess.models.board import Board as Board, Position
from kfchess.models.interfaces import BoardInterface, PieceInterface
from kfchess.models.interfaces import BoardInterface
from kfchess.models.piece import TextPiece as Piece, PieceFactory
from kfchess.models.result import Result
from kfchess.services.interfaces import BoardValidatorInterface


class BoardValidator(BoardValidatorInterface):
    """Validates raw token rows and assembles a Board on success."""

    def validate_and_build(self, raw_board: List[List[str]]) -> 'Result[BoardInterface, str]':
        if not raw_board:
            return Result.fail("EMPTY_BOARD")

        expected_width = len(raw_board[0])
        white_kings = 0
        black_kings = 0
        for row in raw_board:
            if len(row) != expected_width:
                return Result.fail("ROW_WIDTH_MISMATCH")
            for token in row:
                if token != '.' and PieceFactory.from_string(token) is None:
                    return Result.fail("UNKNOWN_TOKEN")
                if token == 'wK':
                    white_kings += 1
                elif token == 'bK':
                    black_kings += 1

        if white_kings != 1 or black_kings != 1:
            return Result.fail("INVALID_KING_COUNT")

        board = Board(rows=len(raw_board), cols=expected_width)
        for r_idx, row in enumerate(raw_board):
            for c_idx, token in enumerate(row):
                if token != '.':
                    board.set_piece(Position(r_idx, c_idx), PieceFactory.from_string(token))

        return Result.ok(board)
