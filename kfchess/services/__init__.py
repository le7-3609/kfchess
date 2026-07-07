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
    MoveValidatorInterface,
    MoveValidatorFactoryInterface,
    PathCheckerInterface,
)
from kfchess.services.parser import SimpleBoardParser
from kfchess.services.validator import BoardValidator
from kfchess.services.printer import ConsoleBoardPrinter
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.game_service import GameService
from kfchess.services.path_checker import PathChecker
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

