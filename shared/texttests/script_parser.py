"""Script parser — parses .kfc text test scripts (Layer 8 Text test runner).

Owns: parsing the .kfc DSL into sections: Board, Commands, and expected output.
Must not own: movement rules, direct Board mutation, or duplicated game logic.

.kfc file format::

    Board:
    wK . . .
    . wR . bK
    Commands:
    click 0 0
    click 0 3
    print board
    Expected:
    wK . . .
    . . . wR
"""

from dataclasses import dataclass, field
from typing import List, Optional

from shared.config.consts import (
    BOARD_ROW_SEPARATOR,
    BOARD_SECTION_HEADER,
    COMMANDS_SECTION_HEADER,
    COMMENT_LINE_PREFIX,
    EXPECTED_SECTION_HEADER,
    FILE_ENCODING,
)

# Internal section tags used while walking the file, not part of the DSL.
_SECTION_BOARD = "board"
_SECTION_COMMANDS = "commands"
_SECTION_EXPECTED = "expected"


@dataclass
class KfcScript:
    """Parsed representation of a .kfc integration test script."""

    board_lines: List[str] = field(default_factory=list)
    """Raw board rows (string lines, not yet tokenized)."""

    commands: List[str] = field(default_factory=list)
    """Ordered list of command strings."""

    expected_output: Optional[str] = None
    """Optional expected stdout output (None means no assertion)."""


class ScriptParser:
    """Parses the .kfc text test script DSL into a KfcScript object."""

    def parse_file(self, path: str) -> KfcScript:
        """Read and parse the .kfc script at *path*."""
        with open(path, encoding=FILE_ENCODING) as f:
            lines = f.readlines()
        return self.parse_lines(lines)

    def parse_lines(self, lines: List[str]) -> KfcScript:
        """Parse a list of string lines into a KfcScript."""
        script = KfcScript()
        section = None

        for line in lines:
            stripped = line.rstrip("\n").rstrip("\r")
            content = stripped.strip()

            if not content or content.startswith(COMMENT_LINE_PREFIX):
                continue  # Skip blank lines and comments.

            if content.startswith(BOARD_SECTION_HEADER):
                section = _SECTION_BOARD
            elif content.startswith(COMMANDS_SECTION_HEADER):
                section = _SECTION_COMMANDS
            elif content.startswith(EXPECTED_SECTION_HEADER):
                section = _SECTION_EXPECTED
            else:
                if section == _SECTION_BOARD:
                    script.board_lines.append(content)
                elif section == _SECTION_COMMANDS:
                    script.commands.append(content)
                elif section == _SECTION_EXPECTED:
                    if script.expected_output is None:
                        script.expected_output = content + BOARD_ROW_SEPARATOR
                    else:
                        script.expected_output += content + BOARD_ROW_SEPARATOR

        return script

    def to_input_lines(self, script: KfcScript) -> List[str]:
        """Convert a KfcScript back to the flat input format expected by the engine."""
        lines: List[str] = [BOARD_SECTION_HEADER + BOARD_ROW_SEPARATOR]
        for row in script.board_lines:
            lines.append(row + BOARD_ROW_SEPARATOR)
        lines.append(COMMANDS_SECTION_HEADER + BOARD_ROW_SEPARATOR)
        for cmd in script.commands:
            lines.append(cmd + BOARD_ROW_SEPARATOR)
        return lines
