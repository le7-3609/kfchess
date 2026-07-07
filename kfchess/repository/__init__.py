"""
kfchess.repository
~~~~~~~~~~~~~~~~~~
Board and game-state persistence interfaces and in-memory implementations.
"""
from kfchess.repository.interfaces import BoardRepositoryInterface, GameStateRepositoryInterface
from kfchess.repository.in_memory import InMemoryBoardRepository, InMemoryGameStateRepository

__all__ = [
    'BoardRepositoryInterface',
    'GameStateRepositoryInterface',
    'InMemoryBoardRepository',
    'InMemoryGameStateRepository',
]
