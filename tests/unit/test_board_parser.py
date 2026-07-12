"""Unit tests for kungfu_chess.io.board_parser."""

import unittest

from kungfu_chess.io.board_parser import BoardParser


class TestBoardParser(unittest.TestCase):

    def setUp(self) -> None:
        self.parser = BoardParser()

    def test_parse_board_and_commands(self) -> None:
        lines = [
            "Board:",
            "wK . . .",
            ". wR . bK",
            "Commands:",
            "click 50 50",
            "print board",
        ]
        board, cmds = self.parser.parse(lines)
        self.assertEqual(board, [["wK", ".", ".", "."], [".", "wR", ".", "bK"]])
        self.assertEqual(cmds, ["click 50 50", "print board"])

    def test_blank_lines_ignored(self) -> None:
        lines = [
            "",
            "Board:",
            "",
            "wK bK",
            "",
            "Commands:",
            "",
            "print board",
        ]
        board, cmds = self.parser.parse(lines)
        self.assertEqual(board, [["wK", "bK"]])
        self.assertEqual(cmds, ["print board"])

    def test_no_board_returns_empty(self) -> None:
        board, cmds = self.parser.parse(["Commands:", "print board"])
        self.assertEqual(board, [])
        self.assertEqual(cmds, ["print board"])

    def test_no_commands_returns_empty(self) -> None:
        board, cmds = self.parser.parse(["Board:", "wK bK"])
        self.assertEqual(board, [["wK", "bK"]])
        self.assertEqual(cmds, [])

    def test_empty_input(self) -> None:
        board, cmds = self.parser.parse([])
        self.assertEqual(board, [])
        self.assertEqual(cmds, [])

    def test_simple_board_alias(self) -> None:
        from kungfu_chess.io.board_parser import SimpleBoardParser
        self.assertIs(SimpleBoardParser, BoardParser)


if __name__ == "__main__":
    unittest.main()
