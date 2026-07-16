import unittest
from kungfu_chess.gui.pillow_renderer import (
    PillowRenderer,
    LEGAL_MOVE_CAPTURE_COLOR,
    LEGAL_MOVE_EMPTY_COLOR,
)
from kungfu_chess.model.position import Position
from kungfu_chess.view.game_snapshot import GameSnapshot, PieceSnapshot
from kungfu_chess.view.piece_visual_state import PieceVisualState


class TestPillowRenderer(unittest.TestCase):

    def test_colors_are_yellow_and_green(self) -> None:
        # Yellow - option (empty), Green - option that kills the enemy (occupied)
        self.assertEqual(LEGAL_MOVE_CAPTURE_COLOR, (20, 255, 47, 160))  # Green
        self.assertEqual(LEGAL_MOVE_EMPTY_COLOR, (255, 246, 79, 160))    # Yellow

    def test_draw_legal_moves(self) -> None:
        renderer = PillowRenderer("")
        renderer.resize(400, 400)

        pos_empty = Position(2, 2)
        pos_occupied = Position(3, 3)
        piece_snap = PieceSnapshot(
            color="b",
            piece_type="P",
            has_moved=False,
            can_select=False,
            can_move=False,
            state=PieceVisualState.IDLE,
            state_elapsed_millis=0,
            state_duration_millis=0,
        )

        snapshot = GameSnapshot(
            rows=8,
            cols=8,
            pieces={pos_occupied: piece_snap},
            selected_pos=None,
            legal_move_targets=(pos_empty, pos_occupied),
            castle_targets=(),
            active_movements=(),
            cooldown_positions=(),
            clock_ms=0,
            game_over=False,
            game_over_reason=None,
            winner=None,
        )

        renderer.draw(snapshot)
        img = renderer.get_image()
        self.assertIsNotNone(img)


if __name__ == "__main__":
    unittest.main()
