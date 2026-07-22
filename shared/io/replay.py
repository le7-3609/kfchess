"""Replay support — command recording and playback (Layer 5/IO).

Belongs around command or event recording, not inside the Board or rules.

Recordings are stored as DSL text rather than pickled command objects so a
replay file stays readable and can be piped straight back through the normal
script path; translation both ways is io/command_parser.py's job.
"""

from typing import List

from shared.config.consts import (
    FILE_ENCODING,
    FILE_MODE_APPEND,
    FILE_MODE_READ,
    LINE_SEPARATOR,
)
from shared.engine.input_commands import GameCommand
from shared.io.command_parser import (
    CommandParseException,
    TextCommandFormatter,
    TextCommandParser,
)


class ReplayWriter:
    def __init__(self, filename: str) -> None:
        self._filename = filename

    def write_command(self, command: GameCommand) -> None:
        with open(self._filename, FILE_MODE_APPEND, encoding=FILE_ENCODING) as f:
            f.write(TextCommandFormatter.format_command(command) + LINE_SEPARATOR)


class ReplayReader:
    def __init__(self, filename: str) -> None:
        self._filename = filename

    def read_commands(self) -> List[GameCommand]:
        """Return every command in the recording, or none if it does not exist.

        A recording is machine-written, so an unparseable line means a corrupt
        or hand-edited file; it is skipped rather than aborting the playback of
        the moves that did survive.
        """
        try:
            with open(self._filename, FILE_MODE_READ, encoding=FILE_ENCODING) as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        return self._parse_recorded_lines(lines)

    @staticmethod
    def _parse_recorded_lines(lines: List[str]) -> List[GameCommand]:
        commands: List[GameCommand] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                commands.append(TextCommandParser.parse_line(line))
            except CommandParseException:
                continue
        return commands


class ReplayEngineDecorator:
    """Decorates a GameEngine to record commands before executing them.

    This ensures replay is an IO concern wrapped around the engine,
    without changing the core engine or board logic.
    """

    def __init__(self, engine, writer: ReplayWriter) -> None:
        self._engine = engine
        self._writer = writer

    def execute_command(self, command: GameCommand) -> None:
        self._writer.write_command(command)
        self._engine.execute_command(command)

    def advance_clock(self, ms: int) -> None:
        self._engine.advance_clock(ms)

    def __getattr__(self, name):
        """ Forwards any other GameEngine attribute/method this decorator does
         not itself define, so it stays a transparent wrapper as the
         decorated interface grows.
         """
        return getattr(self._engine, name)
