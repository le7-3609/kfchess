import unittest
from tests.test_castling import _build_realtime_service

class TestCheckmate(unittest.TestCase):
    def test_fools_mate(self) -> None:
        service, board_repo, state_repo, _ = _build_realtime_service()

        res = service.execute([
            "Board:",
            "wK . . bQ",
            ".  bR . .",
            ".  . .  bK",
            "Commands:",
            "click 350 50",   # select bQ (0, 3)
            "click 150 50",   # move bQ to (0, 1) - checking wK!
            "wait 5000",      # wait for move to complete
        ])
        self.assertTrue(res.is_ok)
        
        state = state_repo.get_state()
        # White has no legal moves. wK cannot capture bQ because bR protects it.
        self.assertTrue(state.game_over)

if __name__ == '__main__':
    unittest.main()
