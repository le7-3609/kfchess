from abc import ABC, abstractmethod
from typing import Optional
from kfchess.models import Board

class BoardRepositoryInterface(ABC):
    @abstractmethod
    def get_board(self) -> Optional[Board]:
        """Retrieve the currently stored chess board."""
        pass

    @abstractmethod
    def save_board(self, board: Board) -> None:
        """Store the given chess board."""
        pass


class InMemoryBoardRepository(BoardRepositoryInterface):
    def __init__(self):
        self._board: Optional[Board] = None

    def get_board(self) -> Optional[Board]:
        return self._board

    def save_board(self, board: Board) -> None:
        self._board = board
