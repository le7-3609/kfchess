"""Unit tests for shared.model.position."""

import unittest

from shared.model.position import Position


class TestPosition(unittest.TestCase):
    def test_creation(self) -> None:
        pos = Position(3, 5)
        self.assertEqual(pos.row, 3)
        self.assertEqual(pos.col, 5)

    def test_equality(self) -> None:
        self.assertEqual(Position(1, 2), Position(1, 2))
        self.assertNotEqual(Position(1, 2), Position(2, 1))

    def test_named_tuple_unpacking(self) -> None:
        row, col = Position(4, 7)
        self.assertEqual(row, 4)
        self.assertEqual(col, 7)

    def test_hashable(self) -> None:
        pos_set = {Position(0, 0), Position(0, 1), Position(0, 0)}
        self.assertEqual(len(pos_set), 2)

    def test_negative_coordinates_allowed(self) -> None:
        """Position is a pure data container — validation lives in Board."""
        pos = Position(-1, -1)
        self.assertEqual(pos.row, -1)


if __name__ == "__main__":
    unittest.main()
