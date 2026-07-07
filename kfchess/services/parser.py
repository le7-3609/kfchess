from typing import List, Tuple

from kfchess.services.interfaces import BoardParserInterface


class SimpleBoardParser(BoardParserInterface):
    """Splits a VPL-style input into raw board token rows and command strings."""

    def parse(self, input_lines: List[str]) -> Tuple[List[List[str]], List[str]]:
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
