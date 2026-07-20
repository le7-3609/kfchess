"""Unit tests for shared.io.board_parser."""

import unittest

from shared.engine.input_commands import ClickCommand, PrintBoardCommand
from shared.io.board_parser import BoardParser
from shared.model.position import Position


class TestBoardParser(unittest.TestCase):

    def setUp(self) -> None:
        self.parser = BoardParser()

    def test_parse_board_and_commands(self) -> None:
        lines = [
            "Board:",
            "wK . . .",
            ". wR . bK",
            "Commands:",
            "click 0 0",
            "print board",
        ]
        board, cmds = self.parser.parse(lines)
        self.assertEqual(board, [["wK", ".", ".", "."], [".", "wR", ".", "bK"]])
        self.assertEqual(cmds, [ClickCommand(Position(0, 0)), PrintBoardCommand()])

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
        self.assertEqual(cmds, [PrintBoardCommand()])

    def test_no_board_returns_empty(self) -> None:
        board, cmds = self.parser.parse(["Commands:", "print board"])
        self.assertEqual(board, [])
        self.assertEqual(cmds, [PrintBoardCommand()])

    def test_no_commands_returns_empty(self) -> None:
        board, cmds = self.parser.parse(["Board:", "wK bK"])
        self.assertEqual(board, [["wK", "bK"]])
        self.assertEqual(cmds, [])

    def test_empty_input(self) -> None:
        board, cmds = self.parser.parse([])
        self.assertEqual(board, [])
        self.assertEqual(cmds, [])


if __name__ == "__main__":
    unittest.main()
