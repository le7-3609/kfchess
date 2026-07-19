"""Command DSL translation — text to GameCommand and back (Layer 7 Text I/O).

Owns: the textual shape of the command DSL ("click 0 0", "wait 200",
"print board") in both directions. Click/right_click arguments are grid
(row, col) cells, matching Position — never pixels.
Must not own: command execution, board parsing, rule logic, or pixel mapping.

This is the only module that knows commands ever had a text form. The engine
and service layers consume GameCommand objects exclusively, so changing the
script format stops here.
"""

from typing import List

from kungfu_chess.engine.input_commands import (
    ClickCommand,
    GameCommand,
    PrintBoardCommand,
    RightClickCommand,
    WaitCommand,
)
from kungfu_chess.model.position import Position


class CommandParseException(Exception):
    """Raised when a line cannot be translated into a GameCommand."""


def _is_command_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


class TextCommandParser:
    """Translates command DSL text into GameCommand objects.

    Strict by design: every entry point raises CommandParseException rather
    than returning a sentinel, so a malformed line is reported where it is
    read instead of vanishing into a no-op deep in the engine. Callers that
    must tolerate junk (io/board_parser.py, which keeps the .kfc contract of
    ignoring unrecognised lines) catch it explicitly, so the tolerance is
    visible at the boundary that chose it rather than buried here.
    """

    @staticmethod
    def parse_line(line: str) -> GameCommand:
        """Translate a single DSL line into its GameCommand.

        Raises:
            CommandParseException: the line is blank, names an unknown
                command, or carries the wrong arguments for its command.
        """
        tokens = line.strip().split()
        if not tokens:
            raise CommandParseException("Empty command line")

        keyword, args = tokens[0].lower(), tokens[1:]
        try:
            return TextCommandParser._build_command(keyword, args)
        except ValueError as exc:
            raise CommandParseException(
                f"Syntax error in command {line.strip()!r}: {exc}"
            ) from exc

    @staticmethod
    def parse_script(lines: List[str]) -> List[GameCommand]:
        """Translate every command line in *lines*, dropping blanks and #-comments.

        Raises:
            CommandParseException: any surviving line fails to parse.
        """
        return [
            TextCommandParser.parse_line(line) for line in lines if _is_command_line(line)
        ]

    @staticmethod
    def _build_command(keyword: str, args: List[str]) -> GameCommand:
        match keyword:
            case "click":
                return ClickCommand(pos=TextCommandParser._parse_cell_args(keyword, args))
            case "right_click":
                return RightClickCommand(pos=TextCommandParser._parse_cell_args(keyword, args))
            case "wait":
                return WaitCommand(ms=TextCommandParser._parse_duration_args(args))
            case "print":
                return TextCommandParser._build_print_command(args)
            case _:
                raise CommandParseException(f"Unknown command: {keyword!r}")

    @staticmethod
    def _parse_cell_args(keyword: str, args: List[str]) -> Position:
        if len(args) != 2:
            raise CommandParseException(
                f"{keyword!r} expects 'row col', got {len(args)} argument(s)"
            )
        return Position(int(args[0]), int(args[1]))

    @staticmethod
    def _parse_duration_args(args: List[str]) -> int:
        if len(args) != 1:
            raise CommandParseException(
                f"'wait' expects 'ms', got {len(args)} argument(s)"
            )
        return int(args[0])

    @staticmethod
    def _build_print_command(args: List[str]) -> PrintBoardCommand:
        if [arg.lower() for arg in args] != ["board"]:
            raise CommandParseException(f"Invalid print target: {' '.join(args)!r}")
        return PrintBoardCommand()


class TextCommandFormatter:
    """Renders a GameCommand back into its DSL line — inverse of TextCommandParser.

    Lives beside the parser so both directions of the format change together.
    Replay recordings stay plain text (see io/replay.py) so a captured session
    remains readable and can be re-run straight back through the parser.
    """

    @staticmethod
    def format_command(command: GameCommand) -> str:
        """Render *command* as the DSL line that parses back into it."""
        match command:
            case ClickCommand(pos):
                return f"click {pos.row} {pos.col}"
            case RightClickCommand(pos):
                return f"right_click {pos.row} {pos.col}"
            case WaitCommand(ms):
                return f"wait {ms}"
            case PrintBoardCommand():
                return "print board"
            case _:
                raise CommandParseException(
                    f"Cannot format unsupported command: {type(command).__name__}"
                )
