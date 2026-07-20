"""ArrayBoard validator — validates raw token rows and builds a ArrayBoard (io layer).

Lives in io because validation is part of the textual board-setup pipeline.
Must not own: movement rules, command execution, rendering, or timing.
"""

from typing import List, Optional

from shared.config import consts
from shared.model.position import Position
from shared.model.board import ArrayBoard, BoardInterface
from shared.model.piece import PieceFactory
from shared.model.game_state import Result


class BoardValidator:
    """Validates raw token rows and assembles a ArrayBoard on success."""

    def __init__(self, require_kings: bool = True) -> None:
        self._require_kings = require_kings

    def validate_and_build(self, raw_board: List[List[str]]) -> 'Result[BoardInterface, str]':
        """Validate *raw_board* and construct a ArrayBoard.

        Returns:
            Result.ok(board) on success, or Result.fail(error_code) on failure.

        Error codes:
            EMPTY_BOARD         — no rows provided.
            ROW_WIDTH_MISMATCH  — rows have different lengths.
            UNKNOWN_TOKEN       — unrecognised piece token.
            INVALID_KING_COUNT  — not exactly one king per colour.
        """
        if not raw_board:
            return Result.fail(consts.ERROR_EMPTY_BOARD)

        error = self._validate(raw_board)
        if error is not None:
            return Result.fail(error)

        return Result.ok(self._build(raw_board))

    def _validate(self, raw_board: List[List[str]]) -> Optional[str]:
        """Return an error code string if *raw_board* is structurally invalid, else None."""
        expected_width = len(raw_board[0])
        white_kings = 0
        black_kings = 0

        for row in raw_board:
            if len(row) != expected_width:
                return consts.ERROR_ROW_WIDTH_MISMATCH
            for token in row:
                if token != consts.EMPTY_SQUARE_TOKEN and PieceFactory.from_string(token) is None:
                    return consts.ERROR_UNKNOWN_TOKEN
                if token == consts.WHITE_KING_TOKEN:
                    white_kings += 1
                elif token == consts.BLACK_KING_TOKEN:
                    black_kings += 1

        if self._require_kings:
            required = consts.REQUIRED_KINGS_PER_COLOR
            if white_kings != required or black_kings != required:
                return consts.ERROR_INVALID_KING_COUNT

        return None

    def _build(self, raw_board: List[List[str]]) -> BoardInterface:
        expected_width = len(raw_board[0])
        board = ArrayBoard(rows=len(raw_board), cols=expected_width)
        for r_idx, row in enumerate(raw_board):
            for c_idx, token in enumerate(row):
                if token != consts.EMPTY_SQUARE_TOKEN:
                    board.set_piece(Position(r_idx, c_idx), PieceFactory.from_string(token))

        return board


