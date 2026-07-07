import unittest
from io import StringIO
import sys
from kfchess.repository import InMemoryBoardRepository
from kfchess.services import SimpleBoardParser, BoardValidator, ConsoleBoardPrinter, GameService

class TestIntegration(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryBoardRepository()
        self.parser = SimpleBoardParser()
        self.validator = BoardValidator()
        self.printer = ConsoleBoardPrinter()
        self.service = GameService(self.repository, self.parser, self.validator, self.printer)

    def run_with_input(self, input_lines: list[str]) -> tuple[bool, str]:
        old_stdout = sys.stdout
        sys.stdout = mystdout = StringIO()
        try:
            res = self.service.execute(input_lines)
            if not res.is_ok:
                sys.stdout.write(f"ERROR {res.error}\n")
            success = res.is_ok
        finally:
            sys.stdout = old_stdout
        return success, mystdout.getvalue()

    def test_canonical_run(self):
        input_lines = [
            "Board:",
            "wK . . .",
            ". wR . bK",
            "Commands:",
            "print board"
        ]
        success, output = self.run_with_input(input_lines)
        self.assertTrue(success)
        self.assertEqual(output, "wK . . .\n. wR . bK\n")

    def test_row_width_mismatch(self):
        input_lines = [
            "Board:",
            "wK . .",
            ". wR . bK",
            "Commands:",
            "print board"
        ]
        success, output = self.run_with_input(input_lines)
        self.assertFalse(success)
        self.assertEqual(output, "ERROR ROW_WIDTH_MISMATCH\n")

    def test_unknown_token(self):
        input_lines = [
            "Board:",
            "wK . . .",
            ". wR . bZ",
            "Commands:",
            "print board"
        ]
        success, output = self.run_with_input(input_lines)
        self.assertFalse(success)
        self.assertEqual(output, "ERROR UNKNOWN_TOKEN\n")


if __name__ == '__main__':
    unittest.main()
