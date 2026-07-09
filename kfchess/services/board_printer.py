import sys
from typing import List

from kfchess.models.interfaces import BoardInterface
from kfchess.models.board import Position
from kfchess.services.interfaces import BoardPrinterInterface


class ConsoleBoardPrinter(BoardPrinterInterface):
    """Writes the board state to stdout, one row per line."""

    def print_board(self, board: BoardInterface) -> None:
        for r in range(board.rows):
            tokens: List[str] = []
            for c in range(board.cols):
                piece = board.get_piece(Position(r, c))
                if piece is None:
                    tokens.append('.')
                else:
                    # Piece interfaces must be stringifiable for the console printer.
                    tokens.append(str(piece))
            sys.stdout.write(" ".join(tokens) + "\n")
