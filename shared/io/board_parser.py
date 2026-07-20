"""Board parser — textual board setup (Layer 7 Text I/O).

Owns: splitting the DSL into its board section and its command section.
Must not own: movement rules, command execution, rendering, or test assertions.
Command line syntax belongs to io/command_parser.py, which this delegates to.
"""

from typing import List, Tuple

from shared.config import consts
from shared.engine.input_commands import GameCommand
from shared.io.command_parser import CommandParseException, TextCommandParser


class BoardParser:
    """Parses the KungFu Chess textual script into (board_token_rows, commands).

    Expected input format::

        Board:
        wK . . .
        . wR . bK
        Commands:
        click 50 50
        wait 1000
        print board
    """

    def parse(self, input_lines: List[str]) -> Tuple[List[List[str]], List[GameCommand]]:
        """Split *input_lines* into raw board token rows and parsed commands.

        Returns:
            A tuple (board_lines, commands) where:
            - board_lines is a list-of-lists of token strings (one list per row).
            - commands is a list of GameCommand objects.
        """
        board_lines: List[List[str]] = []
        commands: List[GameCommand] = []
        in_board = False
        in_commands = False

        for line in input_lines:
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith(consts.BOARD_SECTION_HEADER):
                in_board = True
                in_commands = False
            elif stripped.startswith(consts.COMMANDS_SECTION_HEADER):
                in_board = False
                in_commands = True
            elif in_board:
                tokens = stripped.split()
                if tokens:
                    board_lines.append(tokens)
            elif in_commands:
                self._append_parsed_command(commands, stripped)

        return board_lines, commands

    @staticmethod
    def _append_parsed_command(commands: List[GameCommand], line: str) -> None:
        """Translate *line* and keep it, dropping anything the DSL rejects.

        Unrecognised lines are ignored by design: .kfc scripts and piped stdin
        carry comments and stray headings alongside commands, and one bad line
        must not abort a whole run. TextCommandParser stays strict — this is
        the single boundary that chooses to tolerate its failure.
        """
        try:
            commands.append(TextCommandParser.parse_line(line))
        except CommandParseException:
            return
