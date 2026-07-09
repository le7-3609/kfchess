"""
kfchess.services
~~~~~~~~~~~~~~~~
Service interfaces and their concrete implementations.
"""
from kfchess.services.interfaces import (
    BoardParserInterface,
    BoardValidatorInterface,
    BoardPrinterInterface,
    CommandExecutorInterface,
)
from kfchess.rules.interfaces import (
    MoveValidatorInterface,
    MoveValidatorFactoryInterface,
    PathCheckerInterface,
)
from kfchess.services.board_parser import SimpleBoardParser
from kfchess.services.board_validator import BoardValidator
from kfchess.services.board_printer import ConsoleBoardPrinter
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.game_service import GameService
from kfchess.rules.path_checker import PathChecker
from kfchess.services.movement_manager import (
    MovementManager,
    InstantMovementDuration,
    ChebyshevDistanceDuration,
)

__all__ = [
    'BoardParserInterface',
    'BoardValidatorInterface',
    'BoardPrinterInterface',
    'CommandExecutorInterface',
    'MoveValidatorInterface',
    'MoveValidatorFactoryInterface',
    'PathCheckerInterface',
    'SimpleBoardParser',
    'BoardValidator',
    'ConsoleBoardPrinter',
    'CommandExecutor',
    'GameService',
    'PathChecker',
    'MovementManager',
    'InstantMovementDuration',
    'ChebyshevDistanceDuration',
]

