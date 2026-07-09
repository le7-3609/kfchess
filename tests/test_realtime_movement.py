from kfchess.rules.promotion_rules import StandardPawnPromotion
from kfchess.rules.move_validators import KingMoveValidator, QueenMoveValidator, RookMoveValidator, BishopMoveValidator, KnightMoveValidator, PawnMoveValidator
from kfchess.config.game_config import GameConfig
import unittest

from kfchess.models.board import Position
from kfchess.models.piece import TextPiece as Piece, PieceFactory
from kfchess.repositories.in_memory import InMemoryBoardrepositories, InMemoryGameStaterepositories
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.game_service import GameService
from kfchess.rules.move_validator_factory import MoveValidatorFactory
from kfchess.services.parser import SimpleBoardParser
from kfchess.rules.path_checker import PathChecker
from kfchess.services.printer import ConsoleBoardPrinter
from kfchess.services.validator import BoardValidator
from kfchess.services.movement_manager import MovementManager, ChebyshevDistanceDuration


def _build_realtime_service() -> tuple[GameService, InMemoryBoardrepositories, InMemoryGameStaterepositories, MoveEventPublisher]:
    board_repo = InMemoryBoardrepositories()
    state_repo = InMemoryGameStaterepositories()
    parser = SimpleBoardParser()
    validator = BoardValidator()
    printer = ConsoleBoardPrinter()
    publisher = MoveEventPublisher()
    path_checker = PathChecker()
    _cfg = GameConfig()
    _validators = {
        "K": KingMoveValidator(),
        "Q": QueenMoveValidator(),
        "R": RookMoveValidator(),
        "B": BishopMoveValidator(),
        "N": KnightMoveValidator(),
        "P": PawnMoveValidator(_cfg)
    }
    movement_manager = MovementManager(
        duration_strategy=ChebyshevDistanceDuration(ms_per_square=1000),
        move_event_publisher=publisher,
        path_checker=path_checker,
        promotion_strategy=StandardPawnPromotion(),
        config=_cfg
    )
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
        move_event_publisher=publisher,
        path_checker=path_checker,
        movement_manager=movement_manager,
        config=_cfg,
    )
    service = GameService(board_repo, state_repo, parser, validator, executor)
    return service, board_repo, state_repo, publisher


class TestRealtimeMovement(unittest.TestCase):
    def test_movement_delay_and_arrival(self) -> None:
        """A piece takes time to travel. It only arrives after wait."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # 1. Initialize board: White Rook at (0, 0)
        # Distance to (0, 2) is 2 squares -> Chebyshev distance = 2 -> duration = 2000 ms.
        res = service.execute([
            "Board:",
            "wR . .",
            "Commands:",
            "click 50 50",   # select wR
            "click 250 50",  # move to (0, 2)
            "print board"
        ])
        self.assertTrue(res.is_ok)

        # Verify that before waiting, the board still has the piece at the original position (0, 0)
        board = board_repo.get_board()
        self.assertIsNotNone(board)
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 0)), Piece("w", "R"))
        self.assertIsNone(board.get_piece(Position(0, 2)))

        # 2. Wait 1000 ms (less than 2000 ms arrival)
        res = service.execute([
            "Board:",
            "wR . .",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 1000",
            "print board"
        ])
        self.assertTrue(res.is_ok)

        # Still at original position
        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 0)), Piece("w", "R"))
        self.assertIsNone(board.get_piece(Position(0, 2)))

        # 3. Wait another 1000 ms (total 2000 ms, equal to arrival)
        res = service.execute([
            "Board:",
            "wR . .",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 1000",
            "wait 1000",
            "print board"
        ])
        self.assertTrue(res.is_ok)

        # Arrived at target position!
        board = board_repo.get_board()
        assert board is not None
        self.assertIsNone(board.get_piece(Position(0, 0)))
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "R"))

    def test_cannot_select_moving_piece(self) -> None:
        """A piece currently in transit cannot be selected again."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        service.execute([
            "Board:",
            "wR . .",
            "Commands:",
            "click 50 50",
            "click 250 50",  # starts moving to (0, 2), duration 2000 ms
            "click 50 50",   # try to select again at origin
        ])

        state = state_repo.get_state()
        self.assertIsNone(state.selected_pos)  # Should be None because selecting moving piece is ignored

    def test_capture_at_arrival_time(self) -> None:
        """Capture happens when the piece arrives, not when the move starts."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # White Rook at (0, 0), Black Pawn at (0, 2)
        # Move Rook to (0, 2) -> Chebyshev distance = 2 -> duration = 2000 ms
        service.execute([
            "Board:",
            "wR . bP",
            "Commands:",
            "click 50 50",
            "click 250 50",
        ])

        # Before arrival, bP is still at (0, 2)
        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("b", "P"))
        self.assertEqual(board.get_piece(Position(0, 0)), Piece("w", "R"))

        # Wait 2000 ms
        service.execute([
            "Board:",
            "wR . bP",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 2000",
        ])

        # Now captured!
        board = board_repo.get_board()
        assert board is not None
        self.assertIsNone(board.get_piece(Position(0, 0)))
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "R"))

    def test_aborted_movement_on_capture(self) -> None:
        """If a moving piece is captured at its source position, its movement is aborted."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # White Rook at (0, 0) moving to (0, 2) (duration 2000 ms)
        # Black Queen at (1, 0) captures White Rook at (0, 0) (duration 1000 ms)
        # Queen arrives at (0, 0) at t=1000, capturing the Rook.
        # At t=2000, Rook's move should not resolve (it is captured/dead).
        service.execute([
            "Board:",
            "wR . .",
            "bQ . .",
            "Commands:",
            "click 50 50",
            "click 250 50",  # wR (0,0) -> (0,2) starts at t=0, arrives t=2000
            "click 50 150",
            "click 50 50",   # bQ (1,0) -> (0,0) starts at t=0, arrives t=1000
            "wait 1000",     # bQ arrives at (0,0), captures wR
        ])

        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 0)), Piece("b", "Q"))
        self.assertIsNone(board.get_piece(Position(0, 2)))

        # Now wait another 1000 ms (total 2000 ms since start)
        service.execute([
            "Board:",
            "wR . .",
            "bQ . .",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "click 50 150",
            "click 50 50",
            "wait 1000",
            "wait 1000",
        ])

        board = board_repo.get_board()
        assert board is not None
        # Queen is still at (0, 0), and (0, 2) is still empty (Rook move did not resolve/teleport)
        self.assertEqual(board.get_piece(Position(0, 0)), Piece("b", "Q"))
        self.assertIsNone(board.get_piece(Position(0, 2)))

    def test_cannot_redirect_moving_piece(self) -> None:
        """A piece already in motion cannot be redirected by clicking it again and choosing a new target."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # White Rook at (0, 0).
        # Move Rook to (0, 2) -> Chebyshev distance = 2 -> duration = 2000 ms.
        # At t=1000, attempt to select it at (0, 0) and redirect it to (0, 1).
        service.execute([
            "Board:",
            "wR . . .",
            "Commands:",
            "click 50 50",
            "click 250 50",  # Starts moving to (0, 2), arrives t=2000
            "wait 1000",     # wait 1000 ms (t=1000)
            "click 50 50",   # attempt selection of Rook at origin (0, 0)
            "click 150 50",  # attempt redirect to (0, 1)
        ])

        # At t=1000, selection should remain None and the redirect command is ignored.
        state = state_repo.get_state()
        self.assertIsNone(state.selected_pos)

        # Wait another 1000 ms (total 2000 ms from start)
        service.execute([
            "Board:",
            "wR . . .",
            "Commands:",
            "click 50 50",
            "click 250 50",
            "wait 1000",
            "click 50 50",
            "click 150 50",
            "wait 1000",  # total wait 2000 ms
        ])

        # The Rook should arrive at its original destination (0, 2) and NOT at (0, 1)
        board = board_repo.get_board()
        assert board is not None
        self.assertIsNone(board.get_piece(Position(0, 0)))
        self.assertIsNone(board.get_piece(Position(0, 1)))
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "R"))

    def test_move_again_immediately_after_arrival(self) -> None:
        """A piece can start moving again immediately after it has arrived at its destination."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # Rook at (0, 0) moves to (0, 2) -> duration 2000 ms.
        # It arrives at t=2000.
        # Immediately at t=2000, select it at (0, 2) and move it to (2, 2) -> duration 2000 ms.
        # Wait another 2000 ms (arrives at t=4000).
        service.execute([
            "Board:",
            "wR . .",
            ". . .",
            ". . .",
            "Commands:",
            "click 50 50",
            "click 250 50",  # starts moving to (0, 2)
            "wait 2000",     # arrives at (0, 2) at t=2000
            "click 250 50",  # select at (0, 2)
            "click 250 250", # move to (2, 2)
            "wait 2000",     # arrives at (2, 2) at t=4000
        ])

        # Verify the Rook has successfully arrived at the second destination (2, 2)
        board = board_repo.get_board()
        assert board is not None
        self.assertIsNone(board.get_piece(Position(0, 0)))
        self.assertIsNone(board.get_piece(Position(0, 2)))
        self.assertEqual(board.get_piece(Position(2, 2)), Piece("w", "R"))

    def test_opposite_colors_do_not_move_concurrently(self) -> None:
        """Opposite colors do not move concurrently."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # wR at (0, 0), bR at (2, 0)
        # wR moves to (0, 2) -> duration 2000 ms
        # bR tries to move to (2, 2) -> duration 2000 ms
        service.execute([
            "Board:",
            "wR . .",
            ". . .",
            "bR . .",
            "Commands:",
            "click 50 50",
            "click 250 50",  # wR starts moving
            "click 50 250", # select bR
            "click 250 250",# try to move bR
            "wait 2000",
            "print board"
        ])

        # wR should have arrived at (0, 2), but bR remains at (2, 0)
        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "R"))
        self.assertEqual(board.get_piece(Position(2, 0)), Piece("b", "R"))
        self.assertIsNone(board.get_piece(Position(2, 2)))

    def test_enemy_collision_mid_path(self) -> None:
        """Two opposing pieces moving towards each other collide mid-path; first-mover captures the other."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # wR at (0, 0), bR at (0, 2)
        # wR moves to (0, 2) (duration 2000 ms). Starts at t=0.
        # At t=500, bR moves to (0, 0) to capture wR (duration 2000 ms).
        # They collide at (0, 1) at t=1500.
        # wR (t=0) captures bR (t=500). bR is removed from board, move aborted.
        # wR arrives at (0, 2) at t=2000.
        service.execute([
            "Board:",
            "wR . bR",
            "Commands:",
            "click 50 50",   # select wR at (0, 0)
            "click 250 50",  # move wR to (0, 2) (starts at t=0)
            "wait 500",      # clock = 500 ms
            "click 250 50",  # select bR at (0, 2)
            "click 50 50",   # move bR to (0, 0) (starts at t=500)
            "wait 1500",     # clock = 2000 ms
            "print board"
        ])

        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "R"))
        self.assertIsNone(board.get_piece(Position(0, 0)))
        self.assertIsNone(board.get_piece(Position(0, 1)))

    def test_friendly_piece_landing_aborts(self) -> None:
        """If a friendly piece occupies the destination square when the moving piece arrives, the move is aborted."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # wR at (0, 0), wP at (1, 2)
        # wR moves to (0, 2) -> duration 2000 ms. Starts at t=0.
        # wP moves to (0, 2) -> duration 1000 ms. Starts at t=0.
        # wP arrives at (0, 2) at t=1000.
        # wR arrives at (0, 2) at t=2000, but wP (friendly) is there, so wR's move is aborted.
        service.execute([
            "Board:",
            "wR . .",
            ". . wP",
            "Commands:",
            "click 250 150", # select wP at (1, 2)
            "click 250 50",  # move wP to (0, 2)
            "click 50 50",   # select wR at (0, 0)
            "click 250 50",  # move wR to (0, 2)
            "wait 2000",
            "print board"
        ])

        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "Q"))
        self.assertEqual(board.get_piece(Position(0, 0)), Piece("w", "R"))

    def test_movement_conflict_blocker(self) -> None:
        """If a piece's path becomes blocked during transit, its movement is aborted at arrival."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # wR at (0, 0), wP at (1, 1)
        # wR moves to (0, 2) -> duration 2000 ms. Starts at t=0.
        # wP moves to (0, 1) -> duration 1000 ms. Starts at t=0.
        # wP arrives at (0, 1) at t=1000.
        # wR arrives at (0, 2) at t=2000, but path is blocked by wP at (0, 1), so wR's move is aborted.
        service.execute([
            "Board:",
            "wR . .",
            ". wP .",
            "Commands:",
            "click 150 150", # select wP at (1, 1)
            "click 150 50",  # move wP to (0, 1)
            "click 50 50",   # select wR at (0, 0)
            "click 250 50",  # move wR to (0, 2)
            "wait 2000",
            "print board"
        ])

        board = board_repo.get_board()
        assert board is not None
        self.assertEqual(board.get_piece(Position(0, 1)), Piece("w", "Q"))
        self.assertEqual(board.get_piece(Position(0, 0)), Piece("w", "R"))
        self.assertIsNone(board.get_piece(Position(0, 2)))

    def test_selection_invalidation_on_capture(self) -> None:
        """If a selected piece is captured, the selection is cleared."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # wK at (0, 0), bQ at (0, 2)
        service.execute([
            "Board:",
            "wK . bQ",
            "Commands:",
            "click 250 50",  # select bQ at (0, 2)
            "click 50 50",   # move bQ to (0, 0) (starts at t=0, duration 2000 ms)
            "wait 1000",     # clock = 1000 ms
            "click 50 50",   # select wK at (0, 0)
        ])

        state = state_repo.get_state()
        self.assertEqual(state.selected_pos, Position(0, 0))

        service.execute([
            "Board:",
            "wK . bQ",
            "Commands:",
            "click 250 50",
            "click 50 50",
            "wait 1000",
            "click 50 50",
            "wait 1000",     # clock = 2000 ms, bQ arrives at (0, 0), captures wK
        ])

        state = state_repo.get_state()
        self.assertIsNone(state.selected_pos)

    def test_same_arrival_conflict(self) -> None:
        """Two friendly moves to the same target start at t=0. They collide at arrival; first-mover wins and the other is aborted."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # wR at (0, 0), wQ at (2, 2)
        # wR moves to (0, 2) (starts t=0, arrives t=2000).
        # wQ moves to (0, 2) (starts t=0, arrives t=2000).
        # wR wins because it is registered first.
        # wQ's move is aborted at arrival because of the collision.
        service.execute([
            "Board:",
            "wR . .",
            ". . .",
            ". . wQ",
            "Commands:",
            "click 50 50",   # select wR
            "click 250 50",  # move to (0, 2)
            "click 250 250", # select wQ
            "click 250 50",  # move to (0, 2)
            "wait 2000",
            "print board"
        ])

        board = board_repo.get_board()
        assert board is not None
        # wR arrived at (0, 2), wQ remained at (2, 2) because its move was aborted
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "R"))
        self.assertEqual(board.get_piece(Position(2, 2)), Piece("w", "Q"))
        self.assertIsNone(board.get_piece(Position(0, 0)))
