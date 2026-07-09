"""Script runner — executes .kfc text scripts through the public command path (Layer 8).

Owns: parsing scripts and driving the engine via its public command API.
Must not own: movement rules, direct Board mutation, or duplicated game logic.
"""

import sys
from io import StringIO
from typing import Optional, Tuple

from kungfu_chess.texttests.script_parser import ScriptParser, KfcScript


class ScriptRunner:
    """Runs a KfcScript through the game engine and captures stdout.

    Usage::

        runner = ScriptRunner(service_factory=build_service)
        success, output = runner.run_script(script)
        assert output == script.expected_output
    """

    def __init__(self, service_factory) -> None:
        """
        Args:
            service_factory: A zero-argument callable that returns a fully
                             wired ``GameService``-like object with an
                             ``execute(input_lines)`` method.
        """
        self._service_factory = service_factory

    def run_script(self, script: KfcScript) -> Tuple[bool, str]:
        """Execute *script* and return (success, captured_stdout).

        Args:
            script: A parsed KfcScript.

        Returns:
            A tuple (success, output) where *success* is True iff the engine
            reported no error, and *output* is whatever was written to stdout.
        """
        parser = ScriptParser()
        input_lines = parser.to_input_lines(script)

        service = self._service_factory()

        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            result = service.execute(input_lines)
            if not result.is_ok:
                sys.stdout.write(f"ERROR {result.error}\n")
            success = result.is_ok
        finally:
            sys.stdout = old_stdout

        return success, captured.getvalue()

    def run_file(self, path: str) -> Tuple[bool, str]:
        """Parse *path* as a .kfc script and execute it.

        Returns:
            A tuple (success, captured_stdout).
        """
        parser = ScriptParser()
        script = parser.parse_file(path)
        return self.run_script(script)

    def assert_script(self, script: KfcScript) -> None:
        """Execute *script* and raise AssertionError if output doesn't match expected.

        Only performs the assertion if ``script.expected_output`` is not None.
        """
        success, output = self.run_script(script)
        if script.expected_output is not None:
            if output != script.expected_output:
                raise AssertionError(
                    f"Script output mismatch.\nExpected:\n{script.expected_output!r}\nGot:\n{output!r}"
                )
