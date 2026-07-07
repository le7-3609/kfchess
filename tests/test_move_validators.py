"""Tests for per-piece movement validators (Strategy pattern) and their
integration with CommandExecutor (via the Factory and Observer).

Each piece test covers:
  - At least one canonical legal move.
  - At least one clearly illegal move.

The CommandExecutor integration tests verify that an illegal move attempt
leaves the piece at its origin and keeps the selection active.
"""

import unittest

from kfchess.models.board import Board, Position
from kfchess.models.game_state import GameState
from kfchess.models.piece import Color, Piece, PieceType
from kfchess.repositories.in_memory import InMemoryBoardrepositories, InMemoryGameStaterepositories
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.interfaces import MoveEventListener
from kfchess.services.move_validator_factory import MoveValidatorFactory
from kfchess.services.move_validators import (
    BishopMoveValidator,
    KingMoveValidator,
    KnightMoveValidator,
    PawnMoveValidator,
    QueenMoveValidator,
    RookMoveValidator,
)
from kfchess.services.path_checker import PathChecker
from kfchess.services.printer import ConsoleBoardPrinter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pos(row: int, col: int) -> Position:
    return Position(row, col)


def _make_executor(board: Board) -> tuple[CommandExecutor,
                                          InMemoryBoardrepositories,
                                          InMemoryGameStaterepositories,
                                          MoveEventPublisher]:
    """Wire a full CommandExecutor with a real factory, path checker, and publisher."""
    board_repo = InMemoryBoardrepositories()
    state_repo = InMemoryGameStaterepositories()
    board_repo.save_board(board)
    state_repo.save_state(GameState())

    class _NullPrinter(ConsoleBoardPrinter):
        def print_board(self, board: Board) -> None:  # type: ignore[override]
            pass

    publisher = MoveEventPublisher()
    executor = CommandExecutor(
        board_repo,
        state_repo,
        _NullPrinter(),
        move_validator_factory=MoveValidatorFactory(),
        move_event_publisher=publisher,
        path_checker=PathChecker(),
    )
    return executor, board_repo, state_repo, publisher


# ---------------------------------------------------------------------------
# King
# ---------------------------------------------------------------------------

class TestKingMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = KingMoveValidator()

    # Legal moves — one step in every direction
    def test_one_square_straight(self) -> None:
        self.assertTrue(self.v.is_legal(_pos(4, 4), _pos(4, 5)))   # right
        self.assertTrue(self.v.is_legal(_pos(4, 4), _pos(3, 4)))   # up

    def test_one_square_diagonal(self) -> None:
        self.assertTrue(self.v.is_legal(_pos(4, 4), _pos(5, 5)))
        self.assertTrue(self.v.is_legal(_pos(4, 4), _pos(3, 3)))

    # Illegal moves
    def test_two_squares_straight_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(4, 4), _pos(4, 6)))

    def test_two_squares_diagonal_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(4, 4), _pos(6, 6)))

    def test_no_move_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(3, 3), _pos(3, 3)))

    def test_knight_shape_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(4, 4), _pos(6, 5)))


# ---------------------------------------------------------------------------
# Rook
# ---------------------------------------------------------------------------

class TestRookMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = RookMoveValidator()

    def test_many_squares_along_rank(self) -> None:
        self.assertTrue(self.v.is_legal(_pos(0, 0), _pos(0, 7)))

    def test_many_squares_along_file(self) -> None:
        self.assertTrue(self.v.is_legal(_pos(0, 3), _pos(7, 3)))

    def test_one_square_straight(self) -> None:
        self.assertTrue(self.v.is_legal(_pos(2, 2), _pos(2, 3)))

    # Illegal moves
    def test_diagonal_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(0, 0), _pos(3, 3)))

    def test_no_move_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(5, 5), _pos(5, 5)))

    def test_knight_shape_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(0, 0), _pos(2, 1)))


# ---------------------------------------------------------------------------
# Bishop
# ---------------------------------------------------------------------------

class TestBishopMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = BishopMoveValidator()

    def test_diagonal_many_squares(self) -> None:
        self.assertTrue(self.v.is_legal(_pos(0, 0), _pos(5, 5)))
        self.assertTrue(self.v.is_legal(_pos(7, 7), _pos(3, 3)))

    def test_diagonal_one_square(self) -> None:
        self.assertTrue(self.v.is_legal(_pos(3, 3), _pos(4, 4)))
        self.assertTrue(self.v.is_legal(_pos(3, 3), _pos(2, 4)))

    # Illegal moves
    def test_straight_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(0, 0), _pos(0, 4)))

    def test_no_move_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(3, 3), _pos(3, 3)))

    def test_knight_shape_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(0, 0), _pos(1, 2)))


# ---------------------------------------------------------------------------
# Queen
# ---------------------------------------------------------------------------

class TestQueenMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = QueenMoveValidator()

    def test_straight_many_squares(self) -> None:
        self.assertTrue(self.v.is_legal(_pos(0, 0), _pos(0, 7)))   # Rook-like

    def test_diagonal_many_squares(self) -> None:
        self.assertTrue(self.v.is_legal(_pos(0, 0), _pos(5, 5)))   # Bishop-like

    def test_one_square_any_direction(self) -> None:
        for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1),
                       (-1, -1), (-1, 1), (1, -1), (1, 1)]:
            with self.subTest(dr=dr, dc=dc):
                self.assertTrue(self.v.is_legal(_pos(4, 4), _pos(4 + dr, 4 + dc)))

    # Illegal moves
    def test_no_move_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(3, 3), _pos(3, 3)))

    def test_knight_shape_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(4, 4), _pos(6, 5)))

    def test_irregular_shape_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(0, 0), _pos(2, 3)))


# ---------------------------------------------------------------------------
# Knight
# ---------------------------------------------------------------------------

class TestKnightMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = KnightMoveValidator()

    def test_all_eight_l_shapes(self) -> None:
        offsets = [(-2, -1), (-2, 1), (-1, -2), (-1, 2),
                   (1, -2),  (1, 2),  (2, -1),  (2, 1)]
        for dr, dc in offsets:
            with self.subTest(dr=dr, dc=dc):
                self.assertTrue(self.v.is_legal(_pos(4, 4), _pos(4 + dr, 4 + dc)))

    # Illegal moves
    def test_one_square_straight_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(4, 4), _pos(4, 5)))

    def test_diagonal_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(4, 4), _pos(6, 6)))

    def test_no_move_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(3, 3), _pos(3, 3)))

    def test_two_squares_straight_is_illegal(self) -> None:
        self.assertFalse(self.v.is_legal(_pos(4, 4), _pos(4, 6)))


# ---------------------------------------------------------------------------
# Pawn movement rules
# ---------------------------------------------------------------------------

class TestPawnMoveValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.v = PawnMoveValidator()

    def test_white_pawn_legal_geometry(self) -> None:
        # Move up 1 square
        self.assertTrue(self.v.is_legal(_pos(6, 3), _pos(5, 3), Color.WHITE))
        # Move diagonally up 1 square (left & right)
        self.assertTrue(self.v.is_legal(_pos(6, 3), _pos(5, 2), Color.WHITE))
        self.assertTrue(self.v.is_legal(_pos(6, 3), _pos(5, 4), Color.WHITE))

    def test_white_pawn_illegal_geometry(self) -> None:
        # Move down (opposite direction)
        self.assertFalse(self.v.is_legal(_pos(6, 3), _pos(7, 3), Color.WHITE))
        # Move sideways
        self.assertFalse(self.v.is_legal(_pos(6, 3), _pos(6, 4), Color.WHITE))
        # Move two spaces forward
        self.assertFalse(self.v.is_legal(_pos(6, 3), _pos(4, 3), Color.WHITE))
        # Move diagonally two spaces
        self.assertFalse(self.v.is_legal(_pos(6, 3), _pos(4, 1), Color.WHITE))

    def test_black_pawn_legal_geometry(self) -> None:
        # Move down 1 square
        self.assertTrue(self.v.is_legal(_pos(1, 3), _pos(2, 3), Color.BLACK))
        # Move diagonally down 1 square (left & right)
        self.assertTrue(self.v.is_legal(_pos(1, 3), _pos(2, 2), Color.BLACK))
        self.assertTrue(self.v.is_legal(_pos(1, 3), _pos(2, 4), Color.BLACK))

    def test_black_pawn_illegal_geometry(self) -> None:
        # Move up (opposite direction)
        self.assertFalse(self.v.is_legal(_pos(1, 3), _pos(0, 3), Color.BLACK))
        # Move sideways
        self.assertFalse(self.v.is_legal(_pos(1, 3), _pos(1, 4), Color.BLACK))
        # Move two spaces forward
        self.assertFalse(self.v.is_legal(_pos(1, 3), _pos(3, 3), Color.BLACK))
        # Move diagonally two spaces
        self.assertFalse(self.v.is_legal(_pos(1, 3), _pos(3, 1), Color.BLACK))


# ---------------------------------------------------------------------------
# CommandExecutor integration — illegal moves keep selection
# ---------------------------------------------------------------------------

class TestIllegalMoveKeepsSelection(unittest.TestCase):
    """
    When the selected piece cannot legally reach the target square, the click
    must be silently ignored: the piece stays at its origin and the selection
    remains active.
    """

    def _setup(self, piece_type: PieceType,
               start: Position) -> tuple[CommandExecutor,
                                         InMemoryBoardrepositories,
                                         InMemoryGameStaterepositories]:
        board = Board(8, 8)
        board.set_piece(start, Piece(Color.WHITE, piece_type))
        executor, board_repo, state_repo, _ = _make_executor(board)
        return executor, board_repo, state_repo

    def _click_pos(self, pos: Position) -> str:
        """Convert Position back to a pixel click command (centre of cell)."""
        x = pos.col * 100 + 50
        y = pos.row * 100 + 50
        return f"click {x} {y}"

    def _assert_illegal_move(
        self,
        piece_type: PieceType,
        start: Position,
        illegal_target: Position,
    ) -> None:
        executor, board_repo, state_repo = self._setup(piece_type, start)

        # Select the piece.
        executor.execute_command(self._click_pos(start))
        self.assertEqual(state_repo.get_state().selected_pos, start,
                         "Piece should be selected after first click.")

        # Attempt the illegal move.
        executor.execute_command(self._click_pos(illegal_target))

        board = board_repo.get_board()
        assert board is not None
        # Piece must still be at origin.
        self.assertEqual(board.get_piece(start), Piece(Color.WHITE, piece_type),
                         "Piece must not move on an illegal click.")
        # Target must remain empty.
        self.assertIsNone(board.get_piece(illegal_target),
                          "Target square must remain empty after illegal move.")
        # Selection must still be active.
        self.assertEqual(state_repo.get_state().selected_pos, start,
                         "Selection should persist after an illegal move attempt.")

    def test_king_cannot_move_two_squares(self) -> None:
        self._assert_illegal_move(PieceType.KING, _pos(4, 4), _pos(4, 6))

    def test_rook_cannot_move_diagonally(self) -> None:
        self._assert_illegal_move(PieceType.ROOK, _pos(4, 4), _pos(6, 6))

    def test_bishop_cannot_move_straight(self) -> None:
        self._assert_illegal_move(PieceType.BISHOP, _pos(4, 4), _pos(4, 7))

    def test_queen_cannot_move_in_knight_shape(self) -> None:
        self._assert_illegal_move(PieceType.QUEEN, _pos(4, 4), _pos(6, 5))

    def test_knight_cannot_move_one_square_straight(self) -> None:
        self._assert_illegal_move(PieceType.KNIGHT, _pos(4, 4), _pos(4, 5))


# ---------------------------------------------------------------------------
# CommandExecutor integration — legal moves succeed + observer fires
# ---------------------------------------------------------------------------

class TestLegalMoveSucceedsAndNotifiesObserver(unittest.TestCase):
    """Legal moves must be committed and fire the MoveEventPublisher."""

    class _RecordingListener(MoveEventListener):
        def __init__(self) -> None:
            self.events: list[tuple[Piece, Position, Position]] = []

        def on_move(self, piece: Piece, frm: Position, to: Position) -> None:
            self.events.append((piece, frm, to))

    def test_rook_moves_straight_and_notifies(self) -> None:
        board = Board(8, 8)
        rook = Piece(Color.WHITE, PieceType.ROOK)
        start = _pos(0, 0)
        dest  = _pos(0, 5)
        board.set_piece(start, rook)

        executor, board_repo, state_repo, publisher = _make_executor(board)
        listener = self._RecordingListener()
        publisher.subscribe(listener)

        # Select rook at (0,0) → pixel (50, 50)
        executor.execute_command("click 50 50")
        # Move to (0,5) → pixel (550, 50)
        executor.execute_command("click 550 50")

        updated = board_repo.get_board()
        assert updated is not None
        self.assertIsNone(updated.get_piece(start),   "Origin must be empty.")
        self.assertEqual(updated.get_piece(dest), rook, "Rook must reach destination.")
        self.assertIsNone(state_repo.get_state().selected_pos,
                          "Selection must be cleared after legal move.")

        # Observer must have received exactly one event.
        self.assertEqual(len(listener.events), 1)
        ev_piece, ev_frm, ev_to = listener.events[0]
        self.assertEqual(ev_piece, rook)
        self.assertEqual(ev_frm,  start)
        self.assertEqual(ev_to,   dest)

    def test_knight_moves_l_shape_and_notifies(self) -> None:
        board = Board(8, 8)
        knight = Piece(Color.WHITE, PieceType.KNIGHT)
        start = _pos(4, 4)
        dest  = _pos(2, 5)   # dr=-2, dc=+1  — valid L-shape
        board.set_piece(start, knight)

        executor, board_repo, state_repo, publisher = _make_executor(board)
        listener = self._RecordingListener()
        publisher.subscribe(listener)

        executor.execute_command(f"click {start.col*100+50} {start.row*100+50}")
        executor.execute_command(f"click {dest.col*100+50} {dest.row*100+50}")

        updated = board_repo.get_board()
        assert updated is not None
        self.assertIsNone(updated.get_piece(start))
        self.assertEqual(updated.get_piece(dest), knight)
        self.assertEqual(len(listener.events), 1)


if __name__ == '__main__':
    unittest.main()
