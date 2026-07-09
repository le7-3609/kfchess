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
from kfchess.services.threat_validator import ThreatValidator
from kfchess.services.endgame_validator import EndgameValidator

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
    threat_validator = ThreatValidator(
        move_validator_factory=MoveValidatorFactory(_validators),
        path_checker=path_checker,
        config=_cfg
    )
    endgame_validator = EndgameValidator(
        move_validator_factory=MoveValidatorFactory(_validators),
        path_checker=path_checker,
        movement_manager=movement_manager,
        threat_validator=threat_validator,
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
        threat_validator=threat_validator,
        endgame_validator=endgame_validator
    )
    service = GameService(board_repo, state_repo, parser, validator, executor)
    return service, board_repo, state_repo, publisher

class TestCastling(unittest.TestCase):
    def test_valid_castling(self) -> None:
        service, board_repo, state_repo, _ = _build_realtime_service()

        # Board Setup:
        # wR (0, 0), wK (0, 4) - path clear
        res = service.execute([
            "Board:",
            "wR . . . wK",
            "bK . . . .",
            "Commands:",
            "click 450 50",  # select wK (0, 4)
            "click 50 50",   # click wR (0, 0)
            "wait 5000"
        ])
        self.assertTrue(res.is_ok)
        
        state = state_repo.get_state()
        # Advance time to allow arrival
        board = board_repo.get_board()
        self.assertEqual(board.get_piece(Position(0, 2)), Piece("w", "K"))
        self.assertEqual(board.get_piece(Position(0, 3)), Piece("w", "R"))

    def test_invalid_castling_path_blocked(self) -> None:
        service, board_repo, state_repo, _ = _build_realtime_service()

        res = service.execute([
            "Board:",
            "wR wN . . wK",
            "bK .  . . .",
            "Commands:",
            "click 450 50",  # select wK (0, 4)
            "click 50 50",   # click wR (0, 0)
        ])
        self.assertTrue(res.is_ok)
        
        state = state_repo.get_state()
        # Should not initiate move
        self.assertEqual(len(state.active_movements), 0)

if __name__ == '__main__':
    unittest.main()
