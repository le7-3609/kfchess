"""Unit tests for kungfu_chess.engine.game_engine — GameEngine command dispatching."""

import sys
import unittest
from io import StringIO

from kungfu_chess.bootstrap import build_service, GameService


def _run(service: GameService, input_lines: list) -> tuple:
    """Execute *service* and capture stdout. Returns (success, stdout_text)."""
    old_stdout = sys.stdout
    sys.stdout = captured = StringIO()
    try:
        res = service.execute(input_lines)
        if not res.is_ok:
            sys.stdout.write(f"ERROR {res.error}\n")
        success = res.is_ok
    finally:
        sys.stdout = old_stdout
    return success, captured.getvalue()


class TestGameEngineClickCommand(unittest.TestCase):
    """Test click command dispatching through the full stack."""

    BOARD = [
        "Board:",
        "wK . . .",
        ". wR . bK",
    ]

    def test_select_and_move(self) -> None:
        service = build_service()
        success, output = _run(service, self.BOARD + [
            "Commands:",
            "click 50 50",    # select wK at (0,0)
            "click 150 50",   # move to (0,1)
            "print board",
        ])
        self.assertTrue(success)
        self.assertEqual(output, ". wK . .\n. wR . bK\n")

    def test_click_outside_board_ignored(self) -> None:
        service = build_service()
        success, output = _run(service, self.BOARD + [
            "Commands:",
            "click 999 999",
            "print board",
        ])
        self.assertTrue(success)
        self.assertEqual(output, "wK . . .\n. wR . bK\n")

    def test_click_captures_opponent(self) -> None:
        service = build_service()
        success, output = _run(service, self.BOARD + [
            "Commands:",
            "click 150 150",   # select wR at (1,1)
            "click 350 150",   # move to (1,3) — capture bK
            "print board",
        ])
        self.assertTrue(success)
        self.assertEqual(output, "wK . . .\n. . . wR\n")


class TestGameEngineWaitCommand(unittest.TestCase):
    def test_wait_does_not_change_board(self) -> None:
        service = build_service()
        success, output = _run(service, [
            "Board:",
            "wK .",
            ". bK",
            "Commands:",
            "wait 1000",
            "print board",
        ])
        self.assertTrue(success)
        self.assertEqual(output, "wK .\n. bK\n")

    def test_zero_wait_ignored(self) -> None:
        """wait 0 should be a no-op (clock must not go backward)."""
        service = build_service()
        success, _ = _run(service, [
            "Board:",
            "wK . bK",
            "Commands:",
            "wait 0",
        ])
        self.assertTrue(success)


class TestGameEnginePrintBoard(unittest.TestCase):
    def test_print_board(self) -> None:
        service = build_service()
        success, output = _run(service, [
            "Board:",
            "wK . . .",
            ". wR . bK",
            "Commands:",
            "print board",
        ])
        self.assertTrue(success)
        self.assertEqual(output, "wK . . .\n. wR . bK\n")

    def test_row_width_mismatch_error(self) -> None:
        service = build_service()
        success, output = _run(service, [
            "Board:",
            "wK . .",
            ". wR . bK",
            "Commands:",
            "print board",
        ])
        self.assertFalse(success)
        self.assertEqual(output, "ERROR ROW_WIDTH_MISMATCH\n")

    def test_unknown_token_error(self) -> None:
        service = build_service()
        success, output = _run(service, [
            "Board:",
            "wK . . .",
            ". wR . bZ",
            "Commands:",
            "print board",
        ])
        self.assertFalse(success)
        self.assertEqual(output, "ERROR UNKNOWN_TOKEN\n")


if __name__ == "__main__":
    unittest.main()
