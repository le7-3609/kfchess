# Repository: https://github.com/le7-3609/kfchess

import sys
from pathlib import Path

# Allow running `python /path/to/main.py` from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kfchess.repositories.in_memory import InMemoryBoardRepository, InMemoryGameStateRepository
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.rules.move_validator_factory import MoveValidatorFactory
from kfchess.services.board_parser import SimpleBoardParser
from kfchess.rules.path_checker import PathChecker
from kfchess.services.board_printer import ConsoleBoardPrinter
from kfchess.services.board_validator import BoardValidator
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.game_service import GameService
from kfchess.services.movement_manager import MovementManager, ChebyshevDistanceDuration
from kfchess.services.game_play_state import GamePlayStateFactory
from kfchess.config.game_config import GameConfig
from kfchess.rules.promotion_rules import StandardPawnPromotion
from kfchess.rules.move_validators import (
    KingMoveValidator, QueenMoveValidator, RookMoveValidator,
    BishopMoveValidator, KnightMoveValidator, PawnMoveValidator
)


def main() -> None:
    # ── Composition Root ────────────────────────────────────────────
    # All concrete dependencies are instantiated here and injected down
    # through the layers — nothing inside the layers creates its own deps.

    config = GameConfig()
    
    board_repo  = InMemoryBoardRepository()
    state_repo  = InMemoryGameStateRepository()
    parser      = SimpleBoardParser()
    validator   = BoardValidator()
    printer     = ConsoleBoardPrinter()

    # Observer: no listeners registered by default; extend here for future needs.
    move_event_publisher = MoveEventPublisher()

    # Factory: maps piece_type -> MoveValidatorInterface (Strategy pattern).
    move_validators = {
        "K": KingMoveValidator(),
        "Q": QueenMoveValidator(),
        "R": RookMoveValidator(),
        "B": BishopMoveValidator(),
        "N": KnightMoveValidator(),
        "P": PawnMoveValidator(config=config),
    }
    move_validator_factory = MoveValidatorFactory(validators=move_validators)

    # Board-aware path and capture checker (Strategy pattern).
    path_checker = PathChecker()

    # State & Factory patterns for game play status
    game_play_state_factory = GamePlayStateFactory()

    promotion_strategy = StandardPawnPromotion()

    # Movement Manager (Strategy pattern for duration & real-time movement).
    movement_manager = MovementManager(
        duration_strategy=ChebyshevDistanceDuration(ms_per_square=config.ms_per_square),
        move_event_publisher=move_event_publisher,
        path_checker=path_checker,
        config=config,
        promotion_strategy=promotion_strategy,
    )

    command_executor = CommandExecutor(
        board_repo=board_repo,
        state_repo=state_repo,
        printer=printer,
        move_validator_factory=move_validator_factory,
        move_event_publisher=move_event_publisher,
        path_checker=path_checker,
        config=config,
        movement_manager=movement_manager,
        game_play_state_factory=game_play_state_factory,
    )
    service = GameService(
        board_repo=board_repo,
        state_repo=state_repo,
        parser=parser,
        validator=validator,
        command_executor=command_executor,
    )

    input_lines = sys.stdin.readlines()
    result = service.execute(input_lines)

    if not result.is_ok:
        print(f"ERROR {result.error}")


if __name__ == "__main__":
    main()