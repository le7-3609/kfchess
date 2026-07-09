import unittest
from kfchess.models.board import Position
from kfchess.models.piece import TextPiece as Piece
from tests.test_castling import _build_realtime_service

class TestDrawRules(unittest.TestCase):
    def test_insufficient_material_king_vs_king(self) -> None:
        """Draw by insufficient material: King vs King."""
        service, board_repo, state_repo, _ = _build_realtime_service()
        res = service.execute([
            "Board:",
            "wK . .",
            ".  . .",
            ".  . bK",
            "Commands:",
            "click 50 50",  # select wK
            "click 150 50", # move wK to (1, 0)
            "wait 1000",
        ])
        self.assertTrue(res.is_ok)
        state = state_repo.get_state()
        self.assertTrue(state.game_over)
        self.assertEqual(state.game_over_reason, "insufficient_material")

    def test_insufficient_material_king_bishop_vs_king(self) -> None:
        """Draw by insufficient material: King + Bishop vs King."""
        service, board_repo, state_repo, _ = _build_realtime_service()
        res = service.execute([
            "Board:",
            "wK wB .",
            ".  .  .",
            ".  .  bK",
            "Commands:",
            "click 50 50",
            "click 50 150",
            "wait 1000",
        ])
        self.assertTrue(res.is_ok)
        state = state_repo.get_state()
        self.assertTrue(state.game_over)
        self.assertEqual(state.game_over_reason, "insufficient_material")

    def test_threefold_repetition(self) -> None:
        """Draw by threefold repetition: repeating king movements."""
        service, board_repo, state_repo, _ = _build_realtime_service()
        # Board must not be insufficient material, so we include Rooks.
        res = service.execute([
            "Board:",
            "wK . .",
            "wR . .",
            "bR . .",
            ".  . bK",
            "Commands:",
            # 1. Move wK to (0, 1)
            "click 50 50",
            "click 150 50",
            "wait 2000", # 1000 ms to arrive, 1000 ms cooldown
            # 2. Move wK back to (0, 0) (2nd occurrence)
            "click 150 50",
            "click 50 50",
            "wait 2000",
            # 3. Move wK to (0, 1)
            "click 50 50",
            "click 150 50",
            "wait 2000",
            # 4. Move wK back to (0, 0) (3rd occurrence - game ends here)
            "click 150 50",
            "click 50 50",
            "wait 2000",
        ])
        self.assertTrue(res.is_ok)
        state = state_repo.get_state()
        self.assertTrue(state.game_over)
        self.assertEqual(state.game_over_reason, "threefold_repetition")

    def test_fifty_move_rule(self) -> None:
        """Draw by fifty-move rule: 100 consecutive non-pawn non-capture half-moves."""
        service, board_repo, state_repo, _ = _build_realtime_service()
        # Set up a board that is not insufficient material and king is safe
        res = service.execute([
            "Board:",
            ".  wK .",
            "wR .  .",
            "bR .  .",
            ".  .  bK",
            "Commands:",
            "click 50 150",  # select wR at (1, 0)
            "click 150 150", # start a move to (1, 1)
        ])
        self.assertTrue(res.is_ok)
        
        # Manually set the halfmove clock to 99 in the repository
        state = state_repo.get_state()
        state.halfmove_clock = 99
        state_repo.save_state(state)
        
        # Invoke the executor directly to run the resolving wait command
        executor = service._command_executor
        executor.execute_command("wait 1000")
        
        state = state_repo.get_state()
        self.assertTrue(state.game_over)
        self.assertEqual(state.game_over_reason, "fifty_move_rule")
