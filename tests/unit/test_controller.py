"""Unit tests for shared.input.controller."""

import unittest
from typing import List, Optional, Tuple

from shared.model.board import ArrayBoard, BoardInterface
from shared.model.piece import TextPiece
from shared.model.position import Position
from shared.input.board_mapper import BoardMapper
from shared.input.controller import Controller
from shared.engine.engine_interfaces import BoardRepositoryInterface


class FakeBoardRepository(BoardRepositoryInterface):
    def __init__(self, board: BoardInterface) -> None:
        self._board = board

    def get_board(self) -> Optional[BoardInterface]:
        return self._board

    def save_board(self, board: BoardInterface) -> None:
        self._board = board


class FakeGameEngine:
    """Spy standing in for GameEngine — records request_move calls."""

    def __init__(self) -> None:
        self.requests: List[Tuple[Position, Position]] = []

    def request_move(self, source: Position, destination: Position) -> None:
        self.requests.append((source, destination))


class TestController(unittest.TestCase):
    def setUp(self) -> None:
        # 100px cells, 8x8 board with a white pawn at (6, 0) i.e. pixel (0, 600).
        self.board = ArrayBoard(8, 8)
        self.board.add_piece(Position(6, 0), TextPiece("w", "P"))
        self.board_repo = FakeBoardRepository(self.board)
        self.engine = FakeGameEngine()
        mapper = BoardMapper(cell_size_px=100)
        self.ctrl = Controller(
            board_mapper=mapper, board_repo=self.board_repo, game_engine=self.engine
        )

    def test_first_click_on_piece_selects_it(self) -> None:
        self.ctrl.on_click(50, 650)  # (row 6, col 0)
        self.assertEqual(self.ctrl._selected, Position(6, 0))
        self.assertEqual(self.engine.requests, [])

    def test_first_click_on_empty_cell_is_ignored(self) -> None:
        self.ctrl.on_click(150, 150)  # (row 1, col 1) empty
        self.assertIsNone(self.ctrl._selected)
        self.assertEqual(self.engine.requests, [])

    def test_first_click_outside_board_is_ignored(self) -> None:
        self.ctrl.on_click(-50, -50)
        self.assertIsNone(self.ctrl._selected)
        self.assertEqual(self.engine.requests, [])

    def test_second_click_requests_move_and_clears_selection(self) -> None:
        self.ctrl.on_click(50, 650)   # select (6, 0)
        self.ctrl.on_click(50, 450)   # (row 4, col 0) -> request move
        self.assertIsNone(self.ctrl._selected)
        self.assertEqual(self.engine.requests, [(Position(6, 0), Position(4, 0))])

    def test_second_click_clears_selection_even_if_move_illegal(self) -> None:
        # Same target as source is still an in-board second click; per spec,
        # selection clears regardless of legality — legality is the engine's
        # concern, not the Controller's.
        self.ctrl.on_click(50, 650)
        self.ctrl.on_click(750, 750)  # far-away in-board cell
        self.assertIsNone(self.ctrl._selected)
        self.assertEqual(len(self.engine.requests), 1)

    def test_outside_board_click_with_selection_cancels_and_sends_nothing(self) -> None:
        self.ctrl.on_click(50, 650)  # select (6, 0)
        self.ctrl.on_click(-50, -50)  # outside board
        self.assertIsNone(self.ctrl._selected)
        self.assertEqual(self.engine.requests, [])


if __name__ == "__main__":
    unittest.main()
