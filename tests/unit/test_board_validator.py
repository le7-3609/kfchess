"""Unit tests for kungfu_chess.io.board_validator."""

import unittest

from kungfu_chess.io.board_validator import BoardValidator


class TestBoardValidator(unittest.TestCase):

    def test_production_mode_valid_kings(self) -> None:
        validator = BoardValidator(require_kings=True)
        raw_board = [
            ["wK", ".", "."],
            [".", ".", "bK"]
        ]
        result = validator.validate_and_build(raw_board)
        self.assertTrue(result.is_ok)

    def test_production_mode_invalid_kings(self) -> None:
        validator = BoardValidator(require_kings=True)
        # No kings at all
        raw_board = [
            [".", ".", "."],
            [".", ".", "."]
        ]
        result = validator.validate_and_build(raw_board)
        self.assertFalse(result.is_ok)
        self.assertEqual(result.error, "INVALID_KING_COUNT")

    def test_test_mode_bypasses_king_validation(self) -> None:
        validator = BoardValidator(require_kings=False)
        # No kings at all (allowed in test mode)
        raw_board = [
            [".", ".", "."],
            [".", ".", "."]
        ]
        result = validator.validate_and_build(raw_board)
        self.assertTrue(result.is_ok)


if __name__ == "__main__":
    unittest.main()
