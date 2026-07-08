from kfchess.services.move_validators import KingMoveValidator, QueenMoveValidator, RookMoveValidator, BishopMoveValidator, KnightMoveValidator, PawnMoveValidator
from kfchess.config.game_config import GameConfig
"""Tests for path-blocking and capture behaviour (Iteration 4).

Test philosophy
---------------
* PathChecker unit tests verify the two-method interface in isolation (no
  CommandExecutor involved) using a real Board.
* CommandExecutor integration tests exercise the full click-move pipeline to
  confirm that blocking and capture rules are enforced end-to-end.

Naming conventions from the problem statement
----------------------------------------------
* Turret  = Rook   (slides along rank/file)
* Runner  = Bishop (slides diagonally)
* Knight jumps — never blocked.
"""

import unittest

from kfchess.models.board import Board, Position
from kfchess.models.game_state import GameState
from kfchess.models.piece import TextPiece as Piece, PieceFactory
from kfchess.repositories.in_memory import (
    InMemoryBoardrepositories,
    InMemoryGameStaterepositories,
)
from kfchess.services.command_executor import CommandExecutor
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.interfaces import MoveEventListener
from kfchess.services.move_validator_factory import MoveValidatorFactory
from kfchess.services.path_checker import PathChecker
from kfchess.services.printer import ConsoleBoardPrinter


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _pos(row: int, col: int) -> Position:
    return Position(row, col)


def _piece(color: str, piece_type: str) -> Piece:
    return Piece(color, piece_type)


def _make_executor(
    board: Board,
) -> tuple[CommandExecutor,
           InMemoryBoardrepositories,
           InMemoryGameStaterepositories,
           MoveEventPublisher]:
    """Wire a full CommandExecutor with real factory, path checker, and publisher."""
    board_repo = InMemoryBoardrepositories()
    state_repo = InMemoryGameStaterepositories()
    board_repo.save_board(board)
    state_repo.save_state(GameState())

    class _NullPrinter(ConsoleBoardPrinter):
        def print_board(self, board: Board) -> None:  # type: ignore[override]
            pass

    publisher = MoveEventPublisher()
    _cfg = GameConfig()
    _validators = {
        "K": KingMoveValidator(),
        "Q": QueenMoveValidator(),
        "R": RookMoveValidator(),
        "B": BishopMoveValidator(),
        "N": KnightMoveValidator(),
        "P": PawnMoveValidator(_cfg)
    }
    executor = CommandExecutor(
        board_repo,
        state_repo,
        _NullPrinter(),
        move_validator_factory=MoveValidatorFactory(_validators),
        move_event_publisher=publisher,
        path_checker=PathChecker(),
        config=_cfg,
    )
    return executor, board_repo, state_repo, publisher


def _click(pos: Position) -> str:
    """Return a 'click x y' command targeting the centre pixel of *pos*."""
    x = pos.col * 100 + 50
    y = pos.row * 100 + 50
    return f"click {x} {y}"


# ---------------------------------------------------------------------------
# PathChecker unit tests — is_path_clear
# ---------------------------------------------------------------------------

class TestPathCheckerIsClear(unittest.TestCase):
    """Directly tests PathChecker.is_path_clear without CommandExecutor."""

    def setUp(self) -> None:
        self.checker = PathChecker()
        self.board = Board(8, 8)

    # ── Turret (Rook) ────────────────────────────────────────────────────────

    def test_turret_clear_horizontal_path(self) -> None:
        """Rook has a clear rank — path is free."""
        self.board.set_piece(_pos(0, 0), _piece("w", "R"))
        self.assertTrue(
            self.checker.is_path_clear(self.board, _pos(0, 0), _pos(0, 5)),
        )

    def test_turret_blocked_horizontal_path(self) -> None:
        """A piece on the rank blocks the Rook."""
        self.board.set_piece(_pos(0, 0), _piece("w", "R"))
        self.board.set_piece(_pos(0, 3), _piece("b", "P"))
        self.assertFalse(
            self.checker.is_path_clear(self.board, _pos(0, 0), _pos(0, 5)),
        )

    def test_turret_blocked_vertical_path(self) -> None:
        """A piece on the file blocks the Rook."""
        self.board.set_piece(_pos(0, 2), _piece("w", "R"))
        self.board.set_piece(_pos(3, 2), _piece("w", "P"))
        self.assertFalse(
            self.checker.is_path_clear(self.board, _pos(0, 2), _pos(6, 2)),
        )

    def test_turret_piece_at_destination_does_not_block(self) -> None:
        """The destination square itself is excluded from the path check."""
        self.board.set_piece(_pos(0, 0), _piece("w", "R"))
        self.board.set_piece(_pos(0, 5), _piece("b", "P"))
        # Path (0,1)→(0,4) is clear; (0,5) is the destination — not an obstacle.
        self.assertTrue(
            self.checker.is_path_clear(self.board, _pos(0, 0), _pos(0, 5)),
        )

    # ── Runner (Bishop) ──────────────────────────────────────────────────────

    def test_runner_clear_diagonal_path(self) -> None:
        """Bishop has a clear diagonal — path is free."""
        self.board.set_piece(_pos(0, 0), _piece("w", "B"))
        self.assertTrue(
            self.checker.is_path_clear(self.board, _pos(0, 0), _pos(5, 5)),
        )

    def test_runner_blocked_diagonal_path(self) -> None:
        """A piece on the diagonal blocks the Bishop."""
        self.board.set_piece(_pos(0, 0), _piece("w", "B"))
        self.board.set_piece(_pos(3, 3), _piece("b", "P"))
        self.assertFalse(
            self.checker.is_path_clear(self.board, _pos(0, 0), _pos(5, 5)),
        )

    # ── Knight — always jumps ────────────────────────────────────────────────

    def test_knight_always_clear_regardless_of_obstacles(self) -> None:
        """The Knight ignores any pieces in between its L-shaped path."""
        self.board.set_piece(_pos(4, 4), _piece("w", "N"))
        # Crowd the board with blockers everywhere near the knight.
        for r in range(3, 7):
            for c in range(3, 7):
                if (r, c) != (4, 4):
                    self.board.set_piece(_pos(r, c), _piece("b", "P"))
        # The Knight at (4,4) targets (2,5) — all squares around it are occupied.
        self.assertTrue(
            self.checker.is_path_clear(self.board, _pos(4, 4), _pos(2, 5)),
        )

    # ── King — moves one square, never truly "blocked" ───────────────────────

    def test_king_single_step_is_always_clear(self) -> None:
        """King moves one square so there are no intermediate squares to block."""
        self.board.set_piece(_pos(4, 4), _piece("w", "K"))
        self.assertTrue(
            self.checker.is_path_clear(self.board, _pos(4, 4), _pos(4, 5)),
        )


# ---------------------------------------------------------------------------
# PathChecker unit tests — can_land
# ---------------------------------------------------------------------------

class TestPathCheckerCanLand(unittest.TestCase):
    """Directly tests PathChecker.can_land."""

    def setUp(self) -> None:
        self.checker = PathChecker()
        self.board = Board(8, 8)
        self.white_rook = _piece("w", "R")

    def test_can_land_on_empty_square(self) -> None:
        self.assertTrue(self.checker.can_land(self.board, self.white_rook, _pos(0, 3), _pos(3, 3)))

    def test_cannot_land_on_friendly_piece(self) -> None:
        """Friendly-fire: cannot land where a same-colour piece stands."""
        self.board.set_piece(_pos(3, 3), _piece("w", "P"))
        self.assertFalse(self.checker.can_land(self.board, self.white_rook, _pos(0, 3), _pos(3, 3)))

    def test_can_land_on_enemy_piece(self) -> None:
        """Capture: allowed to land on an enemy-colour piece."""
        self.board.set_piece(_pos(3, 3), _piece("b", "P"))
        self.assertTrue(self.checker.can_land(self.board, self.white_rook, _pos(0, 3), _pos(3, 3)))


# ---------------------------------------------------------------------------
# CommandExecutor integration — blocking keeps selection
# ---------------------------------------------------------------------------

class TestBlockerKeepsSelection(unittest.TestCase):
    """A piece blocked by an intervening piece must not move; selection stays."""

    def _assert_blocked(
        self,
        piece_type: str,
        start: Position,
        blocker_pos: Position,
        target: Position,
        blocker_color: str = "b",
    ) -> None:
        board = Board(8, 8)
        board.set_piece(start, _piece("w", piece_type))
        board.set_piece(blocker_pos, _piece(blocker_color, "P"))

        executor, board_repo, state_repo, _ = _make_executor(board)

        executor.execute_command(_click(start))
        self.assertEqual(state_repo.get_state().selected_pos, start)

        executor.execute_command(_click(target))

        b = board_repo.get_board()
        assert b is not None
        # Piece must remain at origin.
        self.assertEqual(b.get_piece(start), _piece("w", piece_type))
        # Target must remain occupied only by what was there before.
        self.assertIsNone(b.get_piece(target))
        # Selection must be preserved.
        self.assertEqual(state_repo.get_state().selected_pos, start)

    def test_turret_blocked_on_rank(self) -> None:
        """Rook (turret) cannot slide through a blocker on the same rank."""
        # Rook at (0,0), blocker at (0,3), target at (0,6).
        self._assert_blocked("R", _pos(0, 0), _pos(0, 3), _pos(0, 6))

    def test_turret_blocked_on_file(self) -> None:
        """Rook (turret) cannot slide through a blocker on the same file."""
        self._assert_blocked("R", _pos(0, 2), _pos(3, 2), _pos(6, 2))

    def test_runner_blocked_on_diagonal(self) -> None:
        """Bishop (runner) cannot slide through a blocker on its diagonal."""
        self._assert_blocked("B", _pos(0, 0), _pos(3, 3), _pos(5, 5))

    def test_queen_blocked_straight(self) -> None:
        """Queen cannot slide through a blocker on a rank."""
        self._assert_blocked("Q", _pos(4, 0), _pos(4, 2), _pos(4, 5))

    def test_queen_blocked_diagonal(self) -> None:
        """Queen cannot slide through a blocker on a diagonal."""
        self._assert_blocked("Q", _pos(0, 0), _pos(2, 2), _pos(4, 4))


# ---------------------------------------------------------------------------
# CommandExecutor integration — Knight jumps over blockers
# ---------------------------------------------------------------------------

class TestKnightJumpsOverBlockers(unittest.TestCase):
    """Knight must successfully jump over any number of blocking pieces."""

    def test_knight_jumps_over_friendly_blockers(self) -> None:
        board = Board(8, 8)
        knight = _piece("w", "N")
        start = _pos(4, 4)
        dest  = _pos(2, 5)  # L-shape: dr=-2, dc=+1

        board.set_piece(start, knight)
        # Pack all adjacent squares with friendly pawns to simulate a "wall".
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if (dr, dc) != (0, 0):
                    board.set_piece(_pos(4 + dr, 4 + dc), _piece("w", "P"))

        executor, board_repo, state_repo, publisher = _make_executor(board)

        executor.execute_command(_click(start))
        executor.execute_command(_click(dest))

        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(start), "Knight must have left its origin.")
        self.assertEqual(b.get_piece(dest), knight, "Knight must reach destination.")
        self.assertIsNone(state_repo.get_state().selected_pos)

    def test_knight_jumps_over_enemy_blockers(self) -> None:
        board = Board(8, 8)
        knight = _piece("w", "N")
        start = _pos(4, 4)
        dest  = _pos(6, 5)  # L-shape: dr=+2, dc=+1

        board.set_piece(start, knight)
        # Surround with enemy pawns.
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if (dr, dc) != (0, 0):
                    board.set_piece(_pos(4 + dr, 4 + dc), _piece("b", "P"))

        executor, board_repo, state_repo, _ = _make_executor(board)

        executor.execute_command(_click(start))
        executor.execute_command(_click(dest))

        b = board_repo.get_board()
        assert b is not None
        self.assertEqual(b.get_piece(dest), knight)


# ---------------------------------------------------------------------------
# CommandExecutor integration — capture rules
# ---------------------------------------------------------------------------

class TestCaptureRules(unittest.TestCase):
    """Pieces can capture enemies but not allies."""

    class _RecordingListener(MoveEventListener):
        def __init__(self) -> None:
            self.events: list[tuple[Piece, Position, Position]] = []

        def on_move(self, piece: Piece, frm: Position, to: Position) -> None:
            self.events.append((piece, frm, to))

    def test_cannot_capture_friendly_piece(self) -> None:
        """Clicking a friendly piece on the target square replaces the selection."""
        board = Board(8, 8)
        white_rook   = _piece("w", "R")
        white_bishop = _piece("w", "B")
        rook_pos   = _pos(0, 0)
        bishop_pos = _pos(0, 5)

        board.set_piece(rook_pos,   white_rook)
        board.set_piece(bishop_pos, white_bishop)

        executor, board_repo, state_repo, publisher = _make_executor(board)
        listener = self._RecordingListener()
        publisher.subscribe(listener)

        # Select the rook, then click the friendly bishop.
        executor.execute_command(_click(rook_pos))
        self.assertEqual(state_repo.get_state().selected_pos, rook_pos)

        executor.execute_command(_click(bishop_pos))

        b = board_repo.get_board()
        assert b is not None
        # The rook must NOT have moved to bishop_pos.
        self.assertEqual(b.get_piece(rook_pos),   white_rook,   "Rook must stay at origin.")
        self.assertEqual(b.get_piece(bishop_pos), white_bishop, "Bishop must remain in place.")
        # Clicking a friendly replaces the selection — now the bishop is selected.
        self.assertEqual(state_repo.get_state().selected_pos, bishop_pos)
        # No move event must have been fired.
        self.assertEqual(len(listener.events), 0)

    def test_can_capture_enemy_piece(self) -> None:
        """A piece may land on an enemy square, removing the captured piece."""
        board = Board(8, 8)
        white_rook = _piece("w", "R")
        black_pawn = _piece("b", "P")
        rook_pos = _pos(0, 0)
        pawn_pos = _pos(0, 5)

        board.set_piece(rook_pos, white_rook)
        board.set_piece(pawn_pos, black_pawn)

        executor, board_repo, state_repo, publisher = _make_executor(board)
        listener = self._RecordingListener()
        publisher.subscribe(listener)

        executor.execute_command(_click(rook_pos))
        executor.execute_command(_click(pawn_pos))

        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(rook_pos), "Origin must be empty after capture.")
        self.assertEqual(b.get_piece(pawn_pos), white_rook, "Rook must occupy captured square.")
        self.assertIsNone(state_repo.get_state().selected_pos, "Selection must clear after capture.")

        # Observer must receive exactly one move event for the capture.
        self.assertEqual(len(listener.events), 1)
        ev_piece, ev_frm, ev_to = listener.events[0]
        self.assertEqual(ev_piece, white_rook)
        self.assertEqual(ev_frm,  rook_pos)
        self.assertEqual(ev_to,   pawn_pos)

    def test_runner_captures_enemy_on_diagonal(self) -> None:
        """Bishop (runner) captures an enemy piece at the end of its diagonal."""
        board = Board(8, 8)
        white_bishop = _piece("w", "B")
        black_knight = _piece("b", "N")
        bishop_pos = _pos(0, 0)
        target_pos = _pos(5, 5)

        board.set_piece(bishop_pos, white_bishop)
        board.set_piece(target_pos, black_knight)

        executor, board_repo, state_repo, _ = _make_executor(board)

        executor.execute_command(_click(bishop_pos))
        executor.execute_command(_click(target_pos))

        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(bishop_pos))
        self.assertEqual(b.get_piece(target_pos), white_bishop)

    def test_knight_captures_enemy_by_jumping(self) -> None:
        """Knight captures an enemy at the jump destination, ignoring any pieces between."""
        board = Board(8, 8)
        white_knight = _piece("w", "N")
        black_pawn   = _piece("b", "P")
        knight_pos = _pos(4, 4)
        target_pos = _pos(2, 5)

        board.set_piece(knight_pos, white_knight)
        board.set_piece(target_pos, black_pawn)

        executor, board_repo, state_repo, _ = _make_executor(board)

        executor.execute_command(_click(knight_pos))
        executor.execute_command(_click(target_pos))

        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(knight_pos))
        self.assertEqual(b.get_piece(target_pos), white_knight)

    def test_capture_fires_observer(self) -> None:
        """Capturing an enemy piece still fires the MoveEventPublisher."""
        board = Board(8, 8)
        white_queen = _piece("w", "Q")
        black_rook  = _piece("b", "R")
        queen_pos = _pos(3, 0)
        rook_pos  = _pos(3, 6)

        board.set_piece(queen_pos, white_queen)
        board.set_piece(rook_pos,  black_rook)

        executor, _, _, publisher = _make_executor(board)
        listener = self._RecordingListener()
        publisher.subscribe(listener)

        executor.execute_command(_click(queen_pos))
        executor.execute_command(_click(rook_pos))

        self.assertEqual(len(listener.events), 1)
        ev_piece, ev_frm, ev_to = listener.events[0]
        self.assertEqual(ev_piece, white_queen)
        self.assertEqual(ev_frm,   queen_pos)
        self.assertEqual(ev_to,    rook_pos)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):
    """Boundary conditions and compound scenarios."""

    def test_turret_moves_when_path_is_clear_after_gap(self) -> None:
        """Rook moves successfully when the only occupied square is the destination."""
        board = Board(8, 8)
        white_rook = _piece("w", "R")
        black_pawn = _piece("b", "P")
        rook_pos = _pos(0, 0)
        dest_pos = _pos(0, 5)  # Intermediate squares (0,1)–(0,4) are all empty.

        board.set_piece(rook_pos, white_rook)
        board.set_piece(dest_pos, black_pawn)  # Enemy on destination — capture.

        executor, board_repo, _, _ = _make_executor(board)

        executor.execute_command(_click(rook_pos))
        executor.execute_command(_click(dest_pos))

        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(rook_pos))
        self.assertEqual(b.get_piece(dest_pos), white_rook)

    def test_blocker_directly_adjacent_blocks_slider(self) -> None:
        """A blocker in the very first intermediate square stops the slider."""
        board = Board(8, 8)
        white_queen = _piece("w", "Q")
        black_pawn  = _piece("b", "P")
        queen_pos   = _pos(0, 0)
        blocker_pos = _pos(0, 1)  # Immediately adjacent.
        target_pos  = _pos(0, 7)

        board.set_piece(queen_pos,   white_queen)
        board.set_piece(blocker_pos, black_pawn)

        executor, board_repo, state_repo, _ = _make_executor(board)

        executor.execute_command(_click(queen_pos))
        executor.execute_command(_click(target_pos))

        b = board_repo.get_board()
        assert b is not None
        self.assertEqual(b.get_piece(queen_pos), white_queen, "Queen must not have moved.")
        self.assertIsNone(b.get_piece(target_pos))
        self.assertEqual(state_repo.get_state().selected_pos, queen_pos)


# ---------------------------------------------------------------------------
# Soldier (Pawn) Movement and Capture Rules
# ---------------------------------------------------------------------------

class TestSoldierMovementAndCapture(unittest.TestCase):
    """Integration and unit tests for soldier (pawn) movement and capture rules."""

    def test_white_soldier_moves_up_empty_succeeds(self) -> None:
        """White soldier moves 1 square up into an empty square."""
        board = Board(8, 8)
        pawn = _piece("w", "P")
        start = _pos(6, 3)
        target = _pos(5, 3)
        board.set_piece(start, pawn)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(start))
        executor.execute_command(_click(target))

        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(start))
        self.assertEqual(b.get_piece(target), pawn)
        self.assertIsNone(state_repo.get_state().selected_pos)

    def test_black_soldier_moves_down_empty_succeeds(self) -> None:
        """Black soldier moves 1 square down into an empty square."""
        board = Board(8, 8)
        pawn = _piece("b", "P")
        start = _pos(1, 3)
        target = _pos(2, 3)
        board.set_piece(start, pawn)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(start))
        executor.execute_command(_click(target))

        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(start))
        self.assertEqual(b.get_piece(target), pawn)
        self.assertIsNone(state_repo.get_state().selected_pos)

    def test_soldier_can_move_two_spaces_from_start_row(self) -> None:
        """White and Black soldiers can move two spaces from start rows."""
        board = Board(8, 8)
        white_pawn = _piece("w", "P")
        black_pawn = _piece("b", "P")
        board.set_piece(_pos(6, 3), white_pawn)
        board.set_piece(_pos(1, 4), black_pawn)

        executor, board_repo, state_repo, _ = _make_executor(board)

        # White moves 2 spaces
        executor.execute_command(_click(_pos(6, 3)))
        executor.execute_command(_click(_pos(4, 3)))
        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(_pos(6, 3)))
        self.assertEqual(b.get_piece(_pos(4, 3)), white_pawn)

        # Black moves 2 spaces
        executor.execute_command(_click(_pos(1, 4)))
        executor.execute_command(_click(_pos(3, 4)))
        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(_pos(1, 4)))
        self.assertEqual(b.get_piece(_pos(3, 4)), black_pawn)

    def test_soldier_cannot_move_two_spaces_from_non_start_row(self) -> None:
        """Pawn cannot move two spaces if not on its start row."""
        board = Board(8, 8)
        white_pawn = _piece("w", "P")
        board.set_piece(_pos(5, 3), white_pawn)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(_pos(5, 3)))
        executor.execute_command(_click(_pos(3, 3)))
        b = board_repo.get_board()
        assert b is not None
        self.assertEqual(b.get_piece(_pos(5, 3)), white_pawn)
        self.assertIsNone(b.get_piece(_pos(3, 3)))

    def test_soldier_two_spaces_blocked(self) -> None:
        """Pawn 2-square move is blocked if the intermediate square is occupied."""
        board = Board(8, 8)
        white_pawn = _piece("w", "P")
        blocker = _piece("b", "P")
        board.set_piece(_pos(6, 3), white_pawn)
        board.set_piece(_pos(5, 3), blocker)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(_pos(6, 3)))
        executor.execute_command(_click(_pos(4, 3)))
        b = board_repo.get_board()
        assert b is not None
        self.assertEqual(b.get_piece(_pos(6, 3)), white_pawn)
        self.assertIsNone(b.get_piece(_pos(4, 3)))

    def test_soldier_two_spaces_landing_blocked(self) -> None:
        """Pawn 2-square move is blocked if the landing square is occupied."""
        board = Board(8, 8)
        white_pawn = _piece("w", "P")
        blocker = _piece("b", "P")
        board.set_piece(_pos(6, 3), white_pawn)
        board.set_piece(_pos(4, 3), blocker)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(_pos(6, 3)))
        executor.execute_command(_click(_pos(4, 3)))
        b = board_repo.get_board()
        assert b is not None
        self.assertEqual(b.get_piece(_pos(6, 3)), white_pawn)
        self.assertEqual(b.get_piece(_pos(4, 3)), blocker)

    def test_soldier_promotion_to_queen(self) -> None:
        """A pawn reaching the last row becomes a queen and can move as a queen."""
        board = Board(8, 8)
        white_pawn = _piece("w", "P")
        # White pawn starts at row 1, moves to row 0 (last row)
        board.set_piece(_pos(1, 3), white_pawn)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(_pos(1, 3)))
        executor.execute_command(_click(_pos(0, 3)))

        b = board_repo.get_board()
        assert b is not None
        promoted = b.get_piece(_pos(0, 3))
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.piece_type, "Q")
        self.assertEqual(str(promoted), "wQ")

        # Now test that it moves like a Queen (diagonal/straight)
        # Move diagonally from (0,3) to (3,6)
        executor.execute_command(_click(_pos(0, 3)))
        executor.execute_command(_click(_pos(3, 6)))

        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(_pos(0, 3)))
        self.assertEqual(b.get_piece(_pos(3, 6)), promoted)

    def test_soldier_cannot_grab_forward(self) -> None:
        """Soldiers cannot move forward if the square is occupied (grab forward is illegal)."""
        board = Board(8, 8)
        white_pawn = _piece("w", "P")
        enemy_piece = _piece("b", "P")
        start = _pos(6, 3)
        target = _pos(5, 3)
        board.set_piece(start, white_pawn)
        board.set_piece(target, enemy_piece)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(start))
        executor.execute_command(_click(target))

        b = board_repo.get_board()
        assert b is not None
        self.assertEqual(b.get_piece(start), white_pawn)
        self.assertEqual(b.get_piece(target), enemy_piece)

    def test_soldier_grabs_diagonally(self) -> None:
        """Soldier captures diagonally forward."""
        board = Board(8, 8)
        white_pawn = _piece("w", "P")
        enemy_piece = _piece("b", "P")
        start = _pos(6, 3)
        target = _pos(5, 4)
        board.set_piece(start, white_pawn)
        board.set_piece(target, enemy_piece)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(start))
        executor.execute_command(_click(target))

        b = board_repo.get_board()
        assert b is not None
        self.assertIsNone(b.get_piece(start))
        self.assertEqual(b.get_piece(target), white_pawn)

    def test_soldier_cannot_move_diagonally_empty(self) -> None:
        """Soldier cannot move diagonally if the target square is empty."""
        board = Board(8, 8)
        white_pawn = _piece("w", "P")
        start = _pos(6, 3)
        target = _pos(5, 4)
        board.set_piece(start, white_pawn)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(start))
        executor.execute_command(_click(target))

        b = board_repo.get_board()
        assert b is not None
        self.assertEqual(b.get_piece(start), white_pawn)
        self.assertIsNone(b.get_piece(target))

    def test_soldier_cannot_capture_friendly_diagonally(self) -> None:
        """Soldier cannot capture a friendly piece diagonally."""
        board = Board(8, 8)
        white_pawn = _piece("w", "P")
        friendly = _piece("w", "P")
        start = _pos(6, 3)
        target = _pos(5, 4)
        board.set_piece(start, white_pawn)
        board.set_piece(target, friendly)

        executor, board_repo, state_repo, _ = _make_executor(board)
        executor.execute_command(_click(start))
        executor.execute_command(_click(target))

        b = board_repo.get_board()
        assert b is not None
        self.assertEqual(b.get_piece(start), white_pawn)
        self.assertEqual(b.get_piece(target), friendly)


if __name__ == "__main__":
    unittest.main()
