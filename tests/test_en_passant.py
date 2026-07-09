import unittest
from kfchess.models.board import Position
from kfchess.models.piece import TextPiece as Piece
from tests.test_castling import _build_realtime_service

class TestEnPassant(unittest.TestCase):
    def test_en_passant_valid(self) -> None:
        service, board_repo, state_repo, _ = _build_realtime_service()

        res = service.execute([
            "Board:",
            ".  .  .  .",
            ".  .  .  .",
            ".  .  .  .",
            ".  .  .  .",
            ".  bP .  .",
            ".  .  .  .",
            "wP .  .  .",
            ".  .  .  .",
            "Commands:",
            "click 50 650",   # select wP at (6, 0)
            "click 50 450",   # move wP to (4, 0) (2 steps)
            "wait 2000",      # wait for wP to arrive and ghost to be created
            "click 150 450",  # select bP at (4, 1)
            "click 50 550",   # move bP to (5, 0) - en passant square
            "wait 2000",      # wait for bP to arrive and capture
        ])
        self.assertTrue(res.is_ok)
        
        board = board_repo.get_board()
        self.assertIsNone(board.get_piece(Position(4, 0))) # wP was captured!
        self.assertEqual(board.get_piece(Position(5, 0)), Piece("b", "P")) # bP landed here


if __name__ == '__main__':
    unittest.main()
