"""Unit tests for shared.io.command_parser — the text<->GameCommand boundary."""

import dataclasses
import unittest

from shared.bootstrap import build_service
from shared.engine.input_commands import (
    ClickCommand,
    GameCommand,
    PrintBoardCommand,
    RightClickCommand,
    WaitCommand,
)
from shared.io.board_parser import BoardParser
from shared.io.command_parser import (
    CommandParseException,
    TextCommandFormatter,
    TextCommandParser,
)


ALL_COMMANDS = (
    ClickCommand(50, 50),
    RightClickCommand(150, 250),
    WaitCommand(1000),
    PrintBoardCommand(),
)


class TestParseLine(unittest.TestCase):
    def test_parses_each_command_type(self) -> None:
        cases = [
            ("click 50 50", ClickCommand(50, 50)),
            ("right_click 150 250", RightClickCommand(150, 250)),
            ("wait 1000", WaitCommand(1000)),
            ("print board", PrintBoardCommand()),
        ]
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(TextCommandParser.parse_line(line), expected)

    def test_ignores_surrounding_whitespace_and_keyword_case(self) -> None:
        self.assertEqual(TextCommandParser.parse_line("  CLICK 50 50  "), ClickCommand(50, 50))
        self.assertEqual(TextCommandParser.parse_line("Print Board"), PrintBoardCommand())

    def test_pixel_args_map_to_x_then_y(self) -> None:
        command = TextCommandParser.parse_line("click 450 750")
        self.assertEqual((command.x, command.y), (450, 750))

    def test_accepts_negative_coordinates(self) -> None:
        # Off-board clicks are the engine's to reject, not the parser's.
        self.assertEqual(TextCommandParser.parse_line("click -10 -20"), ClickCommand(-10, -20))


class TestParseLineRejections(unittest.TestCase):
    def test_rejects_malformed_lines(self) -> None:
        malformed = [
            "",
            "   ",
            "jump 1 2",
            "click",
            "click 50",
            "click 50 50 50",
            "click a b",
            "wait",
            "wait soon",
            "wait 100 200",
            "print",
            "print pieces",
        ]
        for line in malformed:
            with self.subTest(line=line):
                with self.assertRaises(CommandParseException):
                    TextCommandParser.parse_line(line)

    def test_error_names_the_offending_line(self) -> None:
        with self.assertRaises(CommandParseException) as ctx:
            TextCommandParser.parse_line("click a b")
        self.assertIn("click a b", str(ctx.exception))


class TestParseScript(unittest.TestCase):
    def test_drops_blank_lines_and_comments(self) -> None:
        commands = TextCommandParser.parse_script(
            ["# a comment", "", "click 50 50", "   ", "print board"]
        )
        self.assertEqual(commands, [ClickCommand(50, 50), PrintBoardCommand()])

    def test_raises_on_an_unparseable_line(self) -> None:
        with self.assertRaises(CommandParseException):
            TextCommandParser.parse_script(["click 50 50", "teleport 1 2"])

    def test_empty_script_yields_no_commands(self) -> None:
        self.assertEqual(TextCommandParser.parse_script([]), [])


class TestFormatter(unittest.TestCase):
    def test_round_trips_every_command(self) -> None:
        for command in ALL_COMMANDS:
            with self.subTest(command=command):
                text = TextCommandFormatter.format_command(command)
                self.assertEqual(TextCommandParser.parse_line(text), command)

    def test_rejects_a_command_it_does_not_know(self) -> None:
        class UnknownCommand(GameCommand):
            pass

        with self.assertRaises(CommandParseException):
            TextCommandFormatter.format_command(UnknownCommand())


class TestCommandObjects(unittest.TestCase):
    def test_commands_are_immutable(self) -> None:
        # Frozen so a queued command cannot change between submission and the
        # tick that applies it (runtime/async_runner.py).
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ClickCommand(1, 2).x = 99

    def test_commands_compare_by_value(self) -> None:
        self.assertEqual(ClickCommand(1, 2), ClickCommand(1, 2))
        self.assertNotEqual(ClickCommand(1, 2), ClickCommand(2, 1))

    def test_click_and_right_click_are_distinct_types(self) -> None:
        self.assertNotEqual(ClickCommand(1, 2), RightClickCommand(1, 2))


class TestBoardParserTolerance(unittest.TestCase):
    """The .kfc/stdin contract: unrecognised command lines are skipped, not fatal."""

    def setUp(self) -> None:
        self.parser = BoardParser()

    def test_skips_unparseable_command_lines(self) -> None:
        _board, cmds = self.parser.parse(
            ["Board:", "wK bK", "Commands:", "click 50 50", "not a command", "print board"]
        )
        self.assertEqual(cmds, [ClickCommand(50, 50), PrintBoardCommand()])

    def test_skips_comment_lines_in_the_command_section(self) -> None:
        _board, cmds = self.parser.parse(
            ["Board:", "wK bK", "Commands:", "# select the king", "click 50 50"]
        )
        self.assertEqual(cmds, [ClickCommand(50, 50)])


class TestEngineRejectsUnknownCommands(unittest.TestCase):
    def test_unsupported_command_object_fails_loudly(self) -> None:
        class UnknownCommand(GameCommand):
            pass

        engine = build_service()._engine
        with self.assertRaises(TypeError):
            engine.execute_command(UnknownCommand())


if __name__ == "__main__":
    unittest.main()
