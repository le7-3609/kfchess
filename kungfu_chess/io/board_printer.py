"""Board printer — logical board output (Layer 7 Text I/O).

Owns: writing a textual representation of the board state to an output stream.
Must not own: movement rules, command execution, rendering, or test assertions
              beyond text comparison.
"""

import sys
from typing import List

from kungfu_chess.model.board import BoardInterface
from kungfu_chess.model.position import Position


class BoardPrinter:
    """Writes the board state to stdout, one row per line.

    Output format (same as the input board-description format)::

        wK . . .
        . wR . bK
    """

    def print_board(self, board: BoardInterface) -> None:
        """Print *board* to stdout."""
        for r in range(board.rows):
            tokens: List[str] = []
            for c in range(board.cols):
                piece = board.get_piece(Position(r, c))
                tokens.append('.' if piece is None else str(piece))
            sys.stdout.write(" ".join(tokens) + "\n")


# Alias for backward-compatibility with kfchess imports.
ConsoleBoardPrinter = BoardPrinter
