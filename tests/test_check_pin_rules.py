import unittest
from kfchess.models.board import Position
from kfchess.models.piece import TextPiece as Piece
from kfchess.rules.promotion_rules import StandardPawnPromotion
from kfchess.rules.move_validators import KingMoveValidator, QueenMoveValidator, RookMoveValidator, BishopMoveValidator, KnightMoveValidator, PawnMoveValidator
from kfchess.config.game_config import GameConfig
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


class TestCheckPinRules(unittest.TestCase):
    """
    In Kung Fu Chess, we now enforce strict check and pin rules.
    - A player cannot move a piece that exposes his king to check (Pin Prevention).
    - The king cannot voluntarily move to a threatened square (Self-Check Prevention).
    """

    def test_move_pinned_piece_exposing_king_is_rejected(self) -> None:
        """A piece cannot move if it exposes the king to an enemy piece."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # Board Setup:
        # bR (0, 0), wR (0, 1) - blocking the rook, wK (0, 3)
        res = service.execute([
            "Board:",
            "bR wR . wK",
            ".  .  . .",
            "Commands:",
            "click 150 50",  # select wR (0, 1)
            "click 150 150", # move wR to (1, 1) (invalid, exposes king)
            "wait 1000"      # wait for move to complete
        ])
        self.assertTrue(res.is_ok)

        board = board_repo.get_board()
        assert board is not None
        # wR must not have moved
        self.assertEqual(board.get_piece(Position(0, 1)), Piece("w", "R"))
        self.assertIsNone(board.get_piece(Position(1, 1)))
        
        # Selection should be maintained
        self.assertEqual(state_repo.get_state().selected_pos, Position(0, 1))

    def test_king_cannot_move_into_threat(self) -> None:
        """The king cannot move into a square that is attacked by an enemy piece."""
        service, board_repo, state_repo, _ = _build_realtime_service()

        # Board Setup:
        # bR (0, 0), wK (1, 1)
        # bR controls the 0th row and 0th col.
        # wK attempts to move to (0, 1), stepping directly into the path of bR.
        res = service.execute([
            "Board:",
            "bR .",
            ". wK",
            "Commands:",
            "click 150 150", # select wK (1, 1)
            "click 150 50",  # move wK to (0, 1) (invalid, threatened by bR)
            "wait 1000"      # wait for move to complete
        ])
        self.assertTrue(res.is_ok)

        board = board_repo.get_board()
        assert board is not None
        # King must not have moved
        self.assertEqual(board.get_piece(Position(1, 1)), Piece("w", "K"))
        self.assertIsNone(board.get_piece(Position(0, 1)))
        
        # Selection should be maintained
        self.assertEqual(state_repo.get_state().selected_pos, Position(1, 1))

if __name__ == '__main__':
    unittest.main()
