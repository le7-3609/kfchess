"""Board validator — validates raw token rows and builds a Board (io layer).

Lives in io because validation is part of the textual board-setup pipeline.
Must not own: movement rules, command execution, rendering, or timing.
"""

from typing import List

from kungfu_chess.model.position import Position
from kungfu_chess.model.board import ArrayBoard as Board, BoardInterface
from kungfu_chess.model.piece import PieceFactory
from kungfu_chess.model.game_state import Result


class BoardValidator:
    """Validates raw token rows and assembles a Board on success."""

    def validate_and_build(self, raw_board: List[List[str]]) -> 'Result[BoardInterface, str]':
        """Validate *raw_board* and construct a Board.

        Returns:
            Result.ok(board) on success, or Result.fail(error_code) on failure.

        Error codes:
            EMPTY_BOARD         — no rows provided.
            ROW_WIDTH_MISMATCH  — rows have different lengths.
            UNKNOWN_TOKEN       — unrecognised piece token.
            INVALID_KING_COUNT  — not exactly one king per colour.
        """
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
