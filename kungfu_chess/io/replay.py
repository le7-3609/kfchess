"""Replay support — command recording and playback (Layer 5/IO).

Belongs around command or event recording, not inside the Board or rules.
"""

from typing import List

class ReplayWriter:
    def __init__(self, filename: str) -> None:
        self._filename = filename

    def write_command(self, command: str) -> None:
        """Append a command to the replay file."""
        with open(self._filename, "a", encoding="utf-8") as f:
            f.write(command.strip() + "\n")

class ReplayReader:
    def __init__(self, filename: str) -> None:
        self._filename = filename

    def read_commands(self) -> List[str]:
        """Read all commands from the replay file."""
        try:
            with open(self._filename, "r", encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            return []

class ReplayEngineDecorator:
    """Decorates a GameEngine to record commands before executing them.

    This ensures replay is an IO concern wrapped around the engine,
    without changing the core engine or board logic.
    """
    def __init__(self, engine, writer: ReplayWriter) -> None:
        self._engine = engine
        self._writer = writer

    def execute_command(self, command: str) -> None:
        self._writer.write_command(command)
        self._engine.execute_command(command)
