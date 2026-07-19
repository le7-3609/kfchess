"""Board printer — logical board output (Layer 7 Text I/O).

Owns: writing a textual representation of the board state to an output stream.
Must not own: movement rules, command execution, rendering, or test assertions
              beyond text comparison.
"""

import sys
from typing import List, Optional, TextIO

from kungfu_chess.config import consts
from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.position import Position


class BoardPrinter:
    """Writes the board state to an output stream, one row per line.

    Output format (same as the input board-description format)::

        wK . . .
        . wR . bK
    """

    def __init__(self, stream: Optional[TextIO] = None) -> None:
        self._stream = stream

    def print_board(self, board: BoardInterface) -> None:
        """Print *board* to the configured stream (or sys.stdout, resolved at call time)."""
        stream = self._stream if self._stream is not None else sys.stdout
        for r in range(board.rows):
            tokens: List[str] = []
            for c in range(board.cols):
                piece = board.get_piece(Position(r, c))
                tokens.append(consts.EMPTY_SQUARE_TOKEN if piece is None else str(piece))
            stream.write(consts.BOARD_TOKEN_SEPARATOR.join(tokens) + consts.BOARD_ROW_SEPARATOR)
