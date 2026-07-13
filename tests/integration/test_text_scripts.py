"""Integration tests — execute .kfc scripts through the game engine (Layer 8)."""

import os
import sys
import unittest

from kungfu_chess.bootstrap import build_service
from kungfu_chess.texttests.script_parser import ScriptParser
from kungfu_chess.texttests.script_runner import ScriptRunner

_SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "scripts")


def _runner() -> ScriptRunner:
    return ScriptRunner(service_factory=lambda: build_service(require_kings=False))


class TestTextScripts(unittest.TestCase):
    """Drives all .kfc scripts in the scripts/ directory."""

    def _run_script_file(self, filename: str) -> tuple:
        path = os.path.join(_SCRIPTS_DIR, filename)
        runner = _runner()
        return runner.run_file(path)

    def _assert_script_file(self, filename: str) -> None:
        path = os.path.join(_SCRIPTS_DIR, filename)
        parser = ScriptParser()
        script = parser.parse_file(path)
        runner = _runner()
        runner.assert_script(script)

    # ------------------------------------------------------------------
    # Individual script tests
    # ------------------------------------------------------------------

    def test_01_board_parsing(self) -> None:
        self._assert_script_file("01_board_parsing.kfc")

    def test_02_click_to_move(self) -> None:
        self._assert_script_file("02_click_to_move.kfc")

    def test_03_rook_moves(self) -> None:
        self._assert_script_file("03_rook_moves.kfc")

    def test_04_invalid_moves(self) -> None:
        self._assert_script_file("04_invalid_moves.kfc")

    def test_05_capture(self) -> None:
        self._assert_script_file("05_capture.kfc")

    def test_06_game_over(self) -> None:
        self._assert_script_file("06_game_over.kfc")


if __name__ == "__main__":
    unittest.main()
