"""Unit tests for shared.input.board_mapper."""

import unittest

from shared.model.board import ArrayBoard
from shared.model.position import Position
from shared.input.board_mapper import BoardMapper


class TestBoardMapper(unittest.TestCase):
    def setUp(self) -> None:
        # 100px cell size, 4-col x 2-row board
        self.mapper = BoardMapper(cell_size_px=100)
        self.board = ArrayBoard(2, 4)

    def test_top_left_click(self) -> None:
        pos = self.mapper.pixel_to_position(50, 50, self.board)
        self.assertEqual(pos, Position(0, 0))

    def test_exact_cell_boundary(self) -> None:
        # x=100 is the start of col 1
        pos = self.mapper.pixel_to_position(100, 50, self.board)
        self.assertEqual(pos, Position(0, 1))

    def test_outside_board_returns_none(self) -> None:
        pos = self.mapper.pixel_to_position(999, 999, self.board)
        self.assertIsNone(pos)

    def test_bottom_right_corner(self) -> None:
        # Last valid pixel in last cell: 399, 199 on a 4x2 board with 100px cells
        pos = self.mapper.pixel_to_position(399, 199, self.board)
        self.assertEqual(pos, Position(1, 3))

    def test_second_row(self) -> None:
        pos = self.mapper.pixel_to_position(50, 150, self.board)
        self.assertEqual(pos, Position(1, 0))

    def test_negative_coords_return_none(self) -> None:
        pos = self.mapper.pixel_to_position(-1, 50, self.board)
        self.assertIsNone(pos)


if __name__ == "__main__":
    unittest.main()
