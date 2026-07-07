from typing import List

from kfchess.models.board import Board, Position
from kfchess.models.piece import Piece
from kfchess.models.result import Result
from kfchess.services.interfaces import BoardValidatorInterface


class BoardValidator(BoardValidatorInterface):
    """Validates raw token rows and assembles a Board on success."""

    def validate_and_build(self, raw_board: List[List[str]]) -> 'Result[Board, str]':
        if not raw_board:
            return Result.fail("EMPTY_BOARD")

        expected_width = len(raw_board[0])
        for row in raw_board:
            if len(row) != expected_width:
                return Result.fail("ROW_WIDTH_MISMATCH")
            for token in row:
                if token != '.' and Piece.from_string(token) is None:
                    return Result.fail("UNKNOWN_TOKEN")

        board = Board(rows=len(raw_board), cols=expected_width)
        for r_idx, row in enumerate(raw_board):
            for c_idx, token in enumerate(row):
                if token != '.':
                    board.set_piece(Position(r_idx, c_idx), Piece.from_string(token))

        return Result.ok(board)
