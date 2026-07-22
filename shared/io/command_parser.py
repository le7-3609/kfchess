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

from shared.config import consts
from shared.config.consts import (
    CELL_COMMAND_ARG_COUNT,
    COMMAND_CLICK,
    COMMAND_MOVE,
    COMMAND_PRINT_BOARD,
    COMMAND_RIGHT_CLICK,
    COMMAND_WAIT,
    COMMENT_LINE_PREFIX,
    PRINT_TARGET_BOARD,
    WAIT_COMMAND_ARG_COUNT,
)
from shared.engine.input_commands import (
    ClickCommand,
    GameCommand,
    PrintBoardCommand,
    RequestMoveCommand,
    RightClickCommand,
    WaitCommand,
)
from shared.model.position import Position


class CommandParseException(Exception):
    """Raised when a line cannot be translated into a GameCommand."""


def _is_command_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith(COMMENT_LINE_PREFIX)


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
            case consts.COMMAND_CLICK:
                return ClickCommand(pos=TextCommandParser._parse_cell_args(keyword, args))
            case consts.COMMAND_RIGHT_CLICK:
                return RightClickCommand(pos=TextCommandParser._parse_cell_args(keyword, args))
            case consts.COMMAND_WAIT:
                return WaitCommand(ms=TextCommandParser._parse_duration_args(args))
            case consts.COMMAND_PRINT:
                return TextCommandParser._build_print_command(args)
            case _:
                raise CommandParseException(f"Unknown command: {keyword!r}")

    @staticmethod
    def _parse_cell_args(keyword: str, args: List[str]) -> Position:
        if len(args) != CELL_COMMAND_ARG_COUNT:
            raise CommandParseException(
                f"{keyword!r} expects 'row col', got {len(args)} argument(s)"
            )
        return Position(int(args[0]), int(args[1]))

    @staticmethod
    def _parse_duration_args(args: List[str]) -> int:
        if len(args) != WAIT_COMMAND_ARG_COUNT:
            raise CommandParseException(
                f"'{COMMAND_WAIT}' expects 'ms', got {len(args)} argument(s)"
            )
        return int(args[0])

    @staticmethod
    def _build_print_command(args: List[str]) -> PrintBoardCommand:
        if [arg.lower() for arg in args] != [PRINT_TARGET_BOARD]:
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
                return f"{COMMAND_CLICK} {pos.row} {pos.col}"
            case RequestMoveCommand(source, target):
                return f"{COMMAND_MOVE} {source.row} {source.col} {target.row} {target.col}"
            case RightClickCommand(pos):
                return f"{COMMAND_RIGHT_CLICK} {pos.row} {pos.col}"
            case WaitCommand(ms):
                return f"{COMMAND_WAIT} {ms}"
            case PrintBoardCommand():
                return COMMAND_PRINT_BOARD
            case _:
                raise CommandParseException(
                    f"Cannot format unsupported command: {type(command).__name__}"
                )
