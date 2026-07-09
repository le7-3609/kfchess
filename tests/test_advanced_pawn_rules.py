from kfchess.rules.promotion_rules import StandardPawnPromotion
from kfchess.rules.move_validators import KingMoveValidator, QueenMoveValidator, RookMoveValidator, BishopMoveValidator, KnightMoveValidator, PawnMoveValidator
from kfchess.config.game_config import GameConfig
import unittest

from kfchess.models.board import Board, Position
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
from kfchess.services.game_play_state import GamePlayStateFactory


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
    game_play_state_factory = GamePlayStateFactory()
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
        game_play_state_factory=game_play_state_factory,
    )
    service = GameService(board_repo, state_repo, parser, validator, executor)
    return service, board_repo, state_repo, publisher


class TestAdvancedPawnRules(unittest.TestCase):
    def test_normal_jump_command(self) -> None:
        """A jump command makes the piece airborne for 1000 ms and then land normally in the same cell."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        res = service.execute([
            "Board:",
            ". . .",
            ". wP .",
            "Commands:",
            "jump 150 150",  # Row 1, Col 1
        ])
        self.assertTrue(res.is_ok)

        # Before wait, piece is jumping (airborne) and stays in cell (1, 1)
        board = board_repo.get_board()
        assert board is not None
        piece = board.get_piece(Position(1, 1))
        self.assertIsNotNone(piece)
        assert piece is not None
        self.assertFalse(piece.can_move())
        self.assertFalse(piece.can_select())

        # Wait 500 ms: still jumping
        service.execute([
            "Board:",
            ". . .",
            ". wP .",
            "Commands:",
            "jump 150 150",
            "wait 500"
        ])
        board = board_repo.get_board()
        assert board is not None
        piece = board.get_piece(Position(1, 1))
        assert piece is not None
        self.assertFalse(piece.can_move())

        # Wait 1000 ms (total): piece lands normally, becomes idle (can select/move)
        service.execute([
            "Board:",
            ". . .",
            ". wP .",
            "Commands:",
            "jump 150 150",
            "wait 2000"
        ])
        board = board_repo.get_board()
        assert board is not None
        piece = board.get_piece(Position(1, 1))
        assert piece is not None
        self.assertTrue(piece.can_move())
        self.assertTrue(piece.can_select())

    def test_click_selected_piece_again_triggers_jump(self) -> None:
        """Clicking a selected piece again triggers a jump on that piece."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        res = service.execute([
            "Board:",
            ". . .",
            ". wP .",
            "Commands:",
            "click 150 150",  # Select wP
            "click 150 150",  # Click again -> trigger jump
        ])
        self.assertTrue(res.is_ok)

        # Verify piece is jumping
        board = board_repo.get_board()
        assert board is not None
        piece = board.get_piece(Position(1, 1))
        assert piece is not None
        self.assertFalse(piece.can_move())

        # Wait 1000 ms -> lands
        service.execute([
            "Board:",
            ". . .",
            ". wP .",
            "Commands:",
            "click 150 150",
            "click 150 150",
            "wait 2000"
        ])
        board = board_repo.get_board()
        assert board is not None
        piece = board.get_piece(Position(1, 1))
        assert piece is not None
        self.assertTrue(piece.can_move())

    def test_moving_piece_cannot_jump(self) -> None:
        """A moving piece cannot initiate a jump."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        res = service.execute([
            "Board:",
            ". . . .",
            ". . . .",
            ". wR . .",
            "Commands:",
            "click 150 250",  # Select wR at (2, 1)
            "click 150 50",   # Move to (0, 1) -> duration 2000 ms
            "jump 150 250",   # Attempt to jump moving piece (at original location since it hasn't arrived)
        ])
        self.assertTrue(res.is_ok)

        # Verify that the jump is ignored and the piece is still moving towards (0, 1)
        state = state_repo.get_state()
        # There should only be 1 active movement (the normal move, not a jump)
        self.assertEqual(len(state.active_movements), 1)
        mov = state.active_movements[0]
        self.assertNotEqual(mov.frm, mov.to)

    def test_airborne_piece_captures_arriving_enemy(self) -> None:
        """While airborne, if an enemy moving piece arrives at its cell, the airborne piece captures the arriving enemy."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # wP starts at (2, 2)
        # bR starts at (0, 2) and moves to (2, 2) (duration 2000 ms)
        # At t = 1000 ms, wP jumps in place (lasts 1000 ms, so lands at t = 2000 ms)
        # At t = 2000 ms, bR arrives at (2, 2) while wP is airborne, so wP captures bR
        res = service.execute([
            "Board:",
            ". . bR .",
            ". . . .",
            ". . wP .",
            "Commands:",
            "click 250 50",   # Select bR at (0, 2)
            "click 250 250",  # Move to (2, 2) -> arrives at t = 2000 ms
            "wait 1000",      # Advance clock to 1000 ms
            "jump 250 250",   # wP jumps at (2, 2) -> airborne until t = 2000 ms
            "wait 2000",      # Advance clock to 3000 ms -> collision & arrival check & cooldown
        ])
        self.assertTrue(res.is_ok)

        board = board_repo.get_board()
        assert board is not None
        # Enemy bR is removed from the board (it never landed)
        self.assertIsNone(board.get_piece(Position(0, 2)))
        # wP remains at (2, 2) and lands normally
        piece = board.get_piece(Position(2, 2))
        self.assertEqual(piece, Piece("w", "P"))
        assert piece is not None
        self.assertTrue(piece.can_move())  # Idle again

    def test_airborne_capture_of_king_ends_game(self) -> None:
        """If the captured enemy is a King, the game ends."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # wP starts at (2, 2)
        # bK starts at (1, 2) and moves to (2, 2) (duration 1000 ms)
        # wP jumps in place at (2, 2) (duration 1000 ms)
        # At t = 1000 ms, they collide, and wP captures bK.
        res = service.execute([
            "Board:",
            ". . . .",
            ". . bK .",
            ". . wP .",
            "Commands:",
            "click 250 150",   # Select bK at (1, 2)
            "click 250 250",  # Move to (2, 2) -> duration 1000 ms
            "click 250 250",   # Select wP at (2, 2)
            "click 250 250",   # Jump wP -> duration 1000 ms
            "wait 1000",      # collision & capture bK
        ])
        self.assertTrue(res.is_ok)

        # Game is over because the King was captured!
        state = state_repo.get_state()
        self.assertTrue(state.game_over)

        board = board_repo.get_board()
        assert board is not None
        self.assertIsNone(board.get_piece(Position(1, 2)))
        self.assertEqual(board.get_piece(Position(2, 2)), Piece("w", "P"))

    def test_friendly_arrival_no_capture(self) -> None:
        """A friendly piece arriving at the jumping cell does not get captured by the airborne piece; normal friendly collision rules apply."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # wP starts at (2, 2)
        # wR starts at (0, 2) and moves to (2, 2) (duration 2000 ms)
        # At t = 1000 ms, wP jumps.
        # Since they are of the same color, the later movement (the jump) is aborted or normal collision happens.
        res = service.execute([
            "Board:",
            ". . wR .",
            ". . . .",
            ". . wP .",
            "Commands:",
            "click 250 50",   # Select wR at (0, 2)
            "click 250 250",  # Move to (2, 2)
            "wait 1000",
            "jump 250 250",   # wP jumps
            "wait 1000",      # Collision check
        ])
        self.assertTrue(res.is_ok)

        board = board_repo.get_board()
        assert board is not None
        # Since it is a friendly collision, the later movement (the jump) is aborted.
        # wP remains at (2, 2) and is in IdleState.
        # wR tries to land at (2, 2) but is blocked by friendly wP, so wR lands back at (0, 2)
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "R"))
        self.assertEqual(board.get_piece(Position(2, 2)), Piece("w", "P"))

    def test_airborne_piece_captures_arriving_enemy_instant(self) -> None:
        """User Test 2: wK jumps at (1, 0) and bR moves to (1, 0) instantly. wK captures bR."""
        board_repo = InMemoryBoardrepositories()
        state_repo = InMemoryGameStaterepositories()
        parser = SimpleBoardParser()
        validator = BoardValidator()
        printer = ConsoleBoardPrinter()
        publisher = MoveEventPublisher()
        path_checker = PathChecker()
        
        from kfchess.services.movement_manager import InstantMovementDuration
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
            duration_strategy=InstantMovementDuration(),
            move_event_publisher=publisher,
            path_checker=path_checker,
            promotion_strategy=StandardPawnPromotion(),
            config=_cfg
        )
        game_play_state_factory = GamePlayStateFactory()
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
            game_play_state_factory=game_play_state_factory,
        )
        service = GameService(board_repo, state_repo, parser, validator, executor)

        res = service.execute([
            "Board:",
            ". . .",
            "wK . bR",
            ". . .",
            "Commands:",
            "jump 50 150",
            "click 250 150",
            "click 50 150",
            "wait 1000",
        ])
        self.assertTrue(res.is_ok)
        
        board = board_repo.get_board()
        assert board is not None
        # wK remains on board at (1, 0), bR is removed/captured
        self.assertEqual(board.get_piece(Position(1, 0)), Piece("w", "K"))
        self.assertIsNone(board.get_piece(Position(1, 2)))

    def test_jump_too_late_does_not_save_piece_instant(self) -> None:
        """User Test 3: bR moves to (1, 0) instantly. wK is captured before it can jump."""
        board_repo = InMemoryBoardrepositories()
        state_repo = InMemoryGameStaterepositories()
        parser = SimpleBoardParser()
        validator = BoardValidator()
        printer = ConsoleBoardPrinter()
        publisher = MoveEventPublisher()
        path_checker = PathChecker()
        
        from kfchess.services.movement_manager import InstantMovementDuration
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
            duration_strategy=InstantMovementDuration(),
            move_event_publisher=publisher,
            path_checker=path_checker,
            promotion_strategy=StandardPawnPromotion(),
            config=_cfg
        )
        game_play_state_factory = GamePlayStateFactory()
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
            game_play_state_factory=game_play_state_factory,
        )
        service = GameService(board_repo, state_repo, parser, validator, executor)

        res = service.execute([
            "Board:",
            ". . .",
            "wK . bR",
            ". . .",
            "Commands:",
            "click 250 150",
            "click 50 150",
            "wait 1000",
            "jump 50 150",
        ])
        self.assertTrue(res.is_ok)

        board = board_repo.get_board()
        assert board is not None
        # bR occupies (1, 0), wK is captured and gone
        self.assertEqual(board.get_piece(Position(1, 0)), Piece("b", "R"))
        self.assertIsNone(board.get_piece(Position(1, 2)))


if __name__ == '__main__':
    unittest.main()
