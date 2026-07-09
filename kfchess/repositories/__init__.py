"""
kfchess.repositories
~~~~~~~~~~~~~~~~~~
Board and game-state persistence interfaces and in-memory implementations.
"""
from kfchess.repositories.interfaces import BoardRepositoryInterface, GameStateRepositoryInterface
from kfchess.repositories.in_memory import InMemoryBoardRepository, InMemoryGameStateRepository

__all__ = [
    'BoardRepositoryInterface',
    'GameStateRepositoryInterface',
    'InMemoryBoardRepository',
    'InMemoryGameStateRepository',
]
