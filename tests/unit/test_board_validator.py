"""Unit tests for kungfu_chess.io.board_validator."""

import unittest

from kungfu_chess.io.board_validator import BoardValidator


class TestBoardValidator(unittest.TestCase):

    def test_any_king_count_is_valid(self) -> None:
        validator = BoardValidator()
        
        # Board with exactly 1 white and 1 black king
        raw_board_1 = [
            ["wK", ".", "."],
            [".", ".", "bK"]
        ]
        result_1 = validator.validate_and_build(raw_board_1)
        self.assertTrue(result_1.is_ok)

        # Board with 0 kings
        raw_board_2 = [
            [".", ".", "."],
            [".", ".", "."]
        ]
        result_2 = validator.validate_and_build(raw_board_2)
        self.assertTrue(result_2.is_ok)

        # Board with only 1 white king
        raw_board_3 = [
            ["wK", ".", "."],
            [".", ".", "."]
        ]
        result_3 = validator.validate_and_build(raw_board_3)
        self.assertTrue(result_3.is_ok)


if __name__ == "__main__":
    unittest.main()
