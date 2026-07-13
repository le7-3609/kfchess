"""Unit tests for kungfu_chess.input.controller."""

import unittest

from kungfu_chess.input.board_mapper import BoardMapper
from kungfu_chess.input.controller import Controller


class TestController(unittest.TestCase):
    def setUp(self) -> None:
        mapper = BoardMapper(cell_size_px=100)
        self.ctrl = Controller(board_mapper=mapper)

    def test_on_click_returns_command_string(self) -> None:
        cmd = self.ctrl.on_click(150, 250)
        self.assertEqual(cmd, "click 150 250")

    def test_on_jump_returns_jump_command(self) -> None:
        cmd = self.ctrl.on_jump(50, 50)
        self.assertEqual(cmd, "jump 50 50")


if __name__ == "__main__":
    unittest.main()
