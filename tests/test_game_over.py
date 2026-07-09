import unittest
from kfchess.models.board import Position
from kfchess.models.piece import TextPiece as Piece
from tests.test_castling import _build_realtime_service
import sys
from io import StringIO

class TestGameOver(unittest.TestCase):
    def test_arrival_capture_of_king_ends_game(self) -> None:
        """Capturing the enemy king via normal arrival ends the game and sets game_over to True."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # 2x3 board so King has an escape square and is not in instant checkmate.
        res = service.execute([
            "Board:",
            "wR . bK",
            "wK . .",
            "Commands:",
            "click 50 50",   # select wR
            "click 250 50",  # move to (0, 2)
        ])
        self.assertTrue(res.is_ok)

        # Before arrival, the game is not over
        state = state_repo.get_state()
        self.assertFalse(state.game_over)

        # Wait 2000 ms (arrival) - run it all from the beginning to preserve state correctly
        res = service.execute([
            "Board:",
            "wR . bK",
            "wK . .",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 2000",
        ])
        self.assertTrue(res.is_ok)

        # King is captured, game must be over!
        state = state_repo.get_state()
        self.assertTrue(state.game_over)

        # Verify the King is gone and Rook is at (0, 2)
        board = board_repo.get_board()
        assert board is not None
        self.assertIsNone(board.get_piece(Position(0, 0)))
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "R"))

    def test_collision_capture_of_king_ends_game(self) -> None:
        """Capturing the king at its source while it's in transit ends the game."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # Board where Kings can escape to avoid instant checkmate
        res = service.execute([
            "Board:",
            "bK wR",
            "wP .",
            ".  .",
            "wK .",
            "Commands:",
            "click 150 50",  # select wR (0, 1)
            "click 50 50",   # move wR to (0, 0)
            "click 50 50",   # select bK (0, 0)
            "click 50 150",  # move bK to (1, 0)
            "wait 1000",     # Rook arrives at (0, 0), capturing King at source
        ])
        self.assertTrue(res.is_ok)

        # King captured at source, game must be over!
        state = state_repo.get_state()
        self.assertTrue(state.game_over)

        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 0)), Piece("w", "R"))
        self.assertEqual(board.get_piece(Position(1, 0)), Piece("w", "P"))
        self.assertIsNone(board.get_piece(Position(0, 1)))

    def test_move_commands_ignored_after_game_over(self) -> None:
        """Clicks attempting to select or move are ignored after the game has ended."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # 1. End the game by capturing Black King
        service.execute([
            "Board:",
            "wR . bK",
            "wK . .",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 2000",  # game over here
        ])
    
        state = state_repo.get_state()
        self.assertTrue(state.game_over)
    
        # 2. Try to click and move White Rook from (0, 2) to (0, 1)
        service.execute([
            "Board:",
            "wR . bK",
            "wK . .",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 2000",
            "click 250 50",  # try to select wR at (0, 2)
        ])
    
        # Selection should be ignored (None)
        state = state_repo.get_state()
        self.assertIsNone(state.selected_pos)
    
        # Now try to move it by executing commands
        service.execute([
            "Board:",
            "wR . bK",
            "wK . .",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 2000",
            "click 250 50",  # try to select
            "click 150 50",  # try to move to (0, 1)
            "wait 1000",
        ])
    
        # Board remains unchanged: White Rook is still at (0, 2)
        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "R"))
        self.assertIsNone(board.get_piece(Position(0, 1)))

    def test_wait_and_print_still_work_after_game_over(self) -> None:
        """Wait and print board commands still execute properly after game over."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            res = service.execute([
                "Board:",
                "wR . bK",
                "wK . .",
                "Commands:",
                "click 50 50",
                "click 250 50",
                "wait 2000",  # game ends here
                "wait 500",   # wait works
                "print board",
            ])
        finally:
            sys.stdout = old_stdout
    
        self.assertTrue(res.is_ok)
        self.assertEqual(captured.getvalue(), ". . wR\nwK . .\n")

        state = state_repo.get_state()
        self.assertEqual(state.clock_ms, 2500)

if __name__ == '__main__':
    unittest.main()
