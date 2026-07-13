"""Board parser — textual board setup (Layer 7 Text I/O).

Owns: parsing the DSL board-description section into raw token rows and commands.
Must not own: movement rules, command execution, rendering, or test assertions.
"""

from typing import List, Tuple


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

    def parse(self, input_lines: List[str]) -> Tuple[List[List[str]], List[str]]:
        """Split *input_lines* into raw board token rows and command strings.

        Returns:
            A tuple (board_lines, commands) where:
            - board_lines is a list-of-lists of token strings (one list per row).
            - commands is a list of stripped command strings.
        """
        board_lines: List[List[str]] = []
        commands: List[str] = []
        in_board = False
        in_commands = False

        for line in input_lines:
            stripped = line.strip()
            if not stripped:
                continue

            if stripped.startswith("Board:"):
                in_board = True
                in_commands = False
            elif stripped.startswith("Commands:"):
                in_board = False
                in_commands = True
            elif in_board:
                tokens = stripped.split()
                if tokens:
                    board_lines.append(tokens)
            elif in_commands:
                commands.append(stripped)

        return board_lines, commands
