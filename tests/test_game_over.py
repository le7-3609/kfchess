import unittest

from kfchess.models.board import Position
from kfchess.models.piece import Color, Piece, PieceType
from kfchess.repositories.in_memory import InMemoryBoardrepositories, InMemoryGameStaterepositories
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.game_service import GameService
from kfchess.services.move_validator_factory import MoveValidatorFactory
from kfchess.services.parser import SimpleBoardParser
from kfchess.services.path_checker import PathChecker
from kfchess.services.printer import ConsoleBoardPrinter
from kfchess.services.validator import BoardValidator
from kfchess.services.movement_manager import MovementManager, ChebyshevDistanceDuration
from kfchess.services.game_play_state import GamePlayStateFactory


def _build_realtime_service() -> tuple[GameService, InMemoryBoardrepositories, InMemoryGameStaterepositories, MoveEventPublisher]:
    board_repo = InMemoryBoardrepositories()
    state_repo = InMemoryGameStaterepositories()
    parser = SimpleBoardParser()
    validator = BoardValidator()
    printer = ConsoleBoardPrinter()
    publisher = MoveEventPublisher()
    path_checker = PathChecker()
    movement_manager = MovementManager(
        duration_strategy=ChebyshevDistanceDuration(ms_per_square=1000),
        move_event_publisher=publisher,
        path_checker=path_checker
    )
    game_play_state_factory = GamePlayStateFactory()
    executor = CommandExecutor(
        board_repo,
        state_repo,
        printer,
        move_validator_factory=MoveValidatorFactory(),
        move_event_publisher=publisher,
        path_checker=path_checker,
        movement_manager=movement_manager,
        game_play_state_factory=game_play_state_factory,
    )
    service = GameService(board_repo, state_repo, parser, validator, executor)
    return service, board_repo, state_repo, publisher


class TestGameOver(unittest.TestCase):
    def test_arrival_capture_of_king_ends_game(self) -> None:
        """Capturing the enemy king via normal arrival ends the game and sets game_over to True."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # White Rook at (0, 0), Black King at (0, 2)
        # Move White Rook to (0, 2) -> duration 2000 ms.
        res = service.execute([
            "Board:",
            "wR . bK",
            "Commands:",
            "click 50 50",   # select wR
            "click 250 50",  # move to (0, 2)
        ])
        self.assertTrue(res.is_ok)

        # Before arrival, the game is not over
        state = state_repo.get_state()
        self.assertFalse(state.game_over)

        # Wait 2000 ms (arrival)
        res = service.execute([
            "Board:",
            "wR . bK",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 2000",
        ])
        self.assertTrue(res.is_ok)

        # King is captured, game must be over!
        state = state_repo.get_state()
        self.assertTrue(state.game_over)

        # Verify the King is gone and Rook is at (0, 2)
        board = board_repo.get_board()
        assert board is not None
        self.assertIsNone(board.get_piece(Position(0, 0)))
        self.assertEqual(board.get_piece(Position(0, 2)), Piece(Color.WHITE, PieceType.ROOK))

    def test_collision_capture_of_king_ends_game(self) -> None:
        """Capturing the king at its source while it's in transit ends the game."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # Black King at (0, 0), White Rook at (0, 1), White Pawn at (1, 0)
        # White Rook moves to capture Black King at (0, 0) (duration 1000 ms)
        # Black King moves to capture White Pawn at (1, 0) (duration 1000 ms)
        # Rook is commanded first, so it resolves first at t=1000 and captures the King.
        res = service.execute([
            "Board:",
            "bK wR",
            "wP .",
            "Commands:",
            "click 150 50",  # select wR (0, 1)
            "click 50 50",   # move wR to (0, 0)
            "click 50 50",   # select bK (0, 0)
            "click 50 150",  # move bK to (1, 0)
            "wait 1000",     # Rook arrives at (0, 0), capturing King at source
        ])
        self.assertTrue(res.is_ok)

        # King captured at source, game must be over!
        state = state_repo.get_state()
        self.assertTrue(state.game_over)

        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 0)), Piece(Color.WHITE, PieceType.ROOK))
        self.assertEqual(board.get_piece(Position(1, 0)), Piece(Color.WHITE, PieceType.PAWN))
        self.assertIsNone(board.get_piece(Position(0, 1)))

    def test_move_commands_ignored_after_game_over(self) -> None:
        """Clicks attempting to select or move are ignored after the game has ended."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # 1. End the game by capturing Black King
        service.execute([
            "Board:",
            "wR . bK",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 2000",  # game over here
        ])

        state = state_repo.get_state()
        self.assertTrue(state.game_over)

        # 2. Try to click and move White Rook from (0, 2) to (0, 1)
        service.execute([
            "Board:",
            "wR . bK",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 2000",
            "click 250 50",  # try to select wR at (0, 2)
        ])

        # Selection should be ignored (None)
        state = state_repo.get_state()
        self.assertIsNone(state.selected_pos)

        # Now try to move it by executing commands
        service.execute([
            "Board:",
            "wR . bK",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 2000",
            "click 250 50",  # try to select
            "click 150 50",  # try to move to (0, 1)
            "wait 1000",
        ])

        # Board remains unchanged: White Rook is still at (0, 2)
        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 2)), Piece(Color.WHITE, PieceType.ROOK))
        self.assertIsNone(board.get_piece(Position(0, 1)))

    def test_wait_and_print_still_work_after_game_over(self) -> None:
        """Wait and print board commands still execute properly after game over."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        import sys
        from io import StringIO

        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()
        try:
            res = service.execute([
                "Board:",
                "wR . bK",
                "Commands:",
                "click 50 50",
                "click 250 50",
                "wait 2000",  # game ends here
                "wait 500",   # wait works
                "print board",
            ])
        finally:
            sys.stdout = old_stdout

        self.assertTrue(res.is_ok)
        self.assertEqual(captured.getvalue(), ". . wR\n")

        state = state_repo.get_state()
        self.assertEqual(state.clock_ms, 2500)
