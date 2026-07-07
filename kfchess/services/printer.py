import sys

from kfchess.models.board import Board
from kfchess.services.interfaces import BoardPrinterInterface


class ConsoleBoardPrinter(BoardPrinterInterface):
    """Writes the board state to stdout, one row per line."""

    def print_board(self, board: Board) -> None:
        for r in range(board.rows):
            sys.stdout.write(" ".join(board.get_row_tokens(r)) + "\n")
