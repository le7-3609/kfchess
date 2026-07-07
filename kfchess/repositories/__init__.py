"""
kfchess.repositories
~~~~~~~~~~~~~~~~~~
Board and game-state persistence interfaces and in-memory implementations.
"""
from kfchess.repositories.interfaces import BoardrepositoriesInterface, GameStaterepositoriesInterface
from kfchess.repositories.in_memory import InMemoryBoardrepositories, InMemoryGameStaterepositories

__all__ = [
    'BoardrepositoriesInterface',
    'GameStaterepositoriesInterface',
    'InMemoryBoardrepositories',
    'InMemoryGameStaterepositories',
]
