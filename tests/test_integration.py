from kfchess.rules.move_validators import KingMoveValidator, QueenMoveValidator, RookMoveValidator, BishopMoveValidator, KnightMoveValidator, PawnMoveValidator
from kfchess.config.game_config import GameConfig
import sys
import unittest
from io import StringIO

from kfchess.repositories.in_memory import InMemoryBoardRepository, InMemoryGameStateRepository
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.game_service import GameService
from kfchess.rules.move_validator_factory import MoveValidatorFactory
from kfchess.services.board_parser import SimpleBoardParser
from kfchess.rules.path_checker import PathChecker
from kfchess.services.board_printer import ConsoleBoardPrinter
from kfchess.services.board_validator import BoardValidator


def _build_service() -> GameService:
    """Wire up a fully functional GameService using the console printer."""
    board_repo = InMemoryBoardRepository()
    state_repo = InMemoryGameStateRepository()
    parser = SimpleBoardParser()
    validator = BoardValidator()
    printer = ConsoleBoardPrinter()
    _cfg = GameConfig()
    _validators = {
        "K": KingMoveValidator(),
        "Q": QueenMoveValidator(),
        "R": RookMoveValidator(),
        "B": BishopMoveValidator(),
        "N": KnightMoveValidator(),
        "P": PawnMoveValidator(_cfg)
    }
    executor = CommandExecutor(
        board_repo,
        state_repo,
        printer,
        move_validator_factory=MoveValidatorFactory(_validators),
        move_event_publisher=MoveEventPublisher(),
        path_checker=PathChecker(),
        config=_cfg
    )
    return GameService(board_repo, state_repo, parser, validator, executor)


def _run(service: GameService, input_lines: list[str]) -> tuple[bool, str]:
    """Execute *service* and capture stdout. Returns (success, stdout_text)."""
    old_stdout, sys.stdout = sys.stdout, StringIO()
    try:
        res = service.execute(input_lines)
        if not res.is_ok:
            sys.stdout.write(f"ERROR {res.error}\n")
        success = res.is_ok
    finally:
        sys.stdout = old_stdout  # type: ignore[assignment]
        captured = sys.stdout    # already restored
    return success, old_stdout.getvalue() if False else _run.__dict__.setdefault(
        '_last', sys.stdout)


# Simpler helper that avoids the closure confusion above:

class IntegrationTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.service = _build_service()

    def run_with_input(self, input_lines: list[str]) -> tuple[bool, str]:
        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            res = self.service.execute(input_lines)
            if not res.is_ok:
                sys.stdout.write(f"ERROR {res.error}\n")
            success = res.is_ok
        finally:
            sys.stdout = old_stdout
        return success, captured.getvalue()


# ---------------------------------------------------------------------------
# Iteration 1 — print board
# ---------------------------------------------------------------------------

class TestPrintBoard(IntegrationTestBase):
    def test_canonical_run(self) -> None:
        success, output = self.run_with_input([
            "Board:",
            "wK . . .",
            ". wR . bK",
            "Commands:",
            "print board",
        ])
        self.assertTrue(success)
        self.assertEqual(output, "wK . . .\n. wR . bK\n")

    def test_row_width_mismatch(self) -> None:
        success, output = self.run_with_input([
            "Board:",
            "wK . .",
            ". wR . bK",
            "Commands:",
            "print board",
        ])
        self.assertFalse(success)
        self.assertEqual(output, "ERROR ROW_WIDTH_MISMATCH\n")

    def test_unknown_token(self) -> None:
        success, output = self.run_with_input([
            "Board:",
            "wK . . .",
            ". wR . bZ",
            "Commands:",
            "print board",
        ])
        self.assertFalse(success)
        self.assertEqual(output, "ERROR UNKNOWN_TOKEN\n")


# ---------------------------------------------------------------------------
# Iteration 2 — click
# ---------------------------------------------------------------------------

class TestClickCommand(IntegrationTestBase):
    """
    Board layout used in these tests (each cell is 100×100 px):

        col →  0    1    2    3
    row 0:   wK   .    .    .
    row 1:   .    wR   .    bK
    """
    BOARD = [
        "Board:",
        "wK . . .",
        ". wR . bK",
    ]

    def test_click_selects_then_moves_piece(self) -> None:
        """
        click 50 50   → select wK at (0,0)
        click 150 50  → move wK to (0,1)   [legal: 1 square right]
        print board   → wK should be at col 1, row 0
        """
        success, output = self.run_with_input(
            self.BOARD + [
                "Commands:",
                "click 50 50",    # row=0, col=0 — select wK
                "click 150 50",   # row=0, col=1 — 1 square right (legal)
                "print board",
            ]
        )
        self.assertTrue(success)
        self.assertEqual(output, ". wK . .\n. wR . bK\n")

    def test_click_outside_board_ignored(self) -> None:
        """Clicking well outside the 4×2 board leaves the board unchanged."""
        success, output = self.run_with_input(
            self.BOARD + [
                "Commands:",
                "click 999 999",
                "print board",
            ]
        )
        self.assertTrue(success)
        self.assertEqual(output, "wK . . .\n. wR . bK\n")

    def test_click_replaces_selection(self) -> None:
        """
        click 50 50   → select wK (0,0)
        click 150 150 → select wR (1,1) instead  [friendly White piece]
        click 250 150 → move wR to (1,2)
        print board
        """
        success, output = self.run_with_input(
            self.BOARD + [
                "Commands:",
                "click 50 50",     # select wK
                "click 150 150",   # replace with wR
                "click 250 150",   # move wR to (1,2)
                "print board",
            ]
        )
        self.assertTrue(success)
        self.assertEqual(output, "wK . . .\n. . wR bK\n")

    def test_click_captures_opponent(self) -> None:
        """
        select wR at (1,1), move it to (1,3) to capture bK.
        """
        success, output = self.run_with_input(
            self.BOARD + [
                "Commands:",
                "click 150 150",   # select wR (1,1)
                "click 350 150",   # move to (1,3) — capture bK
                "print board",
            ]
        )
        self.assertTrue(success)
        self.assertEqual(output, "wK . . .\n. . . wR\n")


# ---------------------------------------------------------------------------
# Iteration 2 — wait
# ---------------------------------------------------------------------------

class TestWaitCommand(IntegrationTestBase):
    def test_wait_does_not_change_board(self) -> None:
        """wait only advances the clock; the board must remain unchanged."""
        success, output = self.run_with_input([
            "Board:",
            "wK .",
            ". bK",
            "Commands:",
            "wait 1000",
            "print board",
        ])
        self.assertTrue(success)
        self.assertEqual(output, "wK .\n. bK\n")


# ---------------------------------------------------------------------------
# Iteration 2 — combined sequence
# ---------------------------------------------------------------------------

class TestCombinedCommands(IntegrationTestBase):
    def test_click_wait_click_print(self) -> None:
        """
        Select a piece, wait, then complete the move — board reflects move.
        The move is started after the wait, so print board shows wR still at origin
        (transit hasn't completed yet).
        """
        success, output = self.run_with_input([
            "Board:",
            "wR . .",
            "wK . bK",
            "Commands:",
            "click 50 50",    # select wR at (0,0)
            "wait 500",
            "click 250 50",   # move to (0,2) — legal Rook move (2 squares straight)
            "print board",   # printed BEFORE transit completes
        ])
        self.assertTrue(success)
        self.assertEqual(output, "wR . .\nwK . bK\n")


if __name__ == '__main__':
    unittest.main()
