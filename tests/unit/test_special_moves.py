"""Unit tests for special chess moves: Castling, En Passant, and Promotion in GameEngine/Arbiter."""

import unittest
from kungfu_chess.bootstrap import build_service
from kungfu_chess.model.position import Position
from kungfu_chess.model.piece import TextPiece
from kungfu_chess.model.board import ArrayBoard
from kungfu_chess.model.game_state import GameState, Movement, Cooldown, EnPassantTarget


class TestSpecialMoves(unittest.TestCase):

    def setUp(self) -> None:
        self.service = build_service()
        self.board = ArrayBoard(8, 8)
        self.state = GameState()
        self.service._board_repo.save_board(self.board)
        self.service._state_repo.save_state(self.state)

    def _clear_board(self) -> None:
        for r in range(self.board.rows):
            for c in range(self.board.cols):
                self.board.set_piece(Position(r, c), None)

    # -------------------------------------------------------------------------
    # Castling Tests
    # -------------------------------------------------------------------------

    def test_castling_fails_if_different_ranks(self) -> None:
        self._clear_board()
        k_pos = Position(7, 4)
        r_pos = Position(6, 7)
        self.board.set_piece(k_pos, TextPiece("w", "K"))
        self.board.set_piece(r_pos, TextPiece("w", "R"))
        self.service._board_repo.save_board(self.board)

        self.service._engine.execute_command("click 450 750")  # select King at (7,4)
        self.service._engine.execute_command("click 750 650")  # click Rook at (6,7)

        self.assertEqual(self.board.get_piece(k_pos).piece_type, "K")
        self.assertEqual(self.board.get_piece(r_pos).piece_type, "R")
        self.assertEqual(len(self.service._engine._arbiter.movements()), 0)

    def test_castling_fails_if_king_or_rook_not_idle(self) -> None:
        self._clear_board()
        k_pos = Position(7, 4)
        r_pos = Position(7, 7)
        king = TextPiece("w", "K")
        rook = TextPiece("w", "R")

        self.board.set_piece(k_pos, king)
        self.board.set_piece(r_pos, rook)
        
        king.transition_to_cooldown()
        self.state.active_cooldowns.append(Cooldown(king, 2000))
        
        self.service._board_repo.save_board(self.board)
        self.service._state_repo.save_state(self.state)

        self.service._engine.execute_command("click 450 750")
        self.service._engine.execute_command("click 750 750")

        self.assertEqual(self.board.get_piece(k_pos).piece_type, "K")
        self.assertEqual(self.board.get_piece(r_pos).piece_type, "R")

    # -------------------------------------------------------------------------
    # En Passant Tests
    # -------------------------------------------------------------------------

    def test_en_passant_cleans_up_captured_piece_state(self) -> None:
        self._clear_board()
        w_pawn = TextPiece("w", "P")
        b_pawn = TextPiece("b", "P")

        # White pawn at (3, 4), Black pawn at (3, 3) (moved 2 steps from (1, 3))
        self.board.set_piece(Position(3, 4), w_pawn)
        self.board.set_piece(Position(3, 3), b_pawn)
        
        b_pawn.transition_to_cooldown()
        self.state.active_cooldowns.append(Cooldown(b_pawn, 2000))

        ep_target = EnPassantTarget(pos=Position(2, 3), capture_pos=Position(3, 3), expires_ms=1000)
        self.state.en_passant_targets.append(ep_target)

        self.service._board_repo.save_board(self.board)
        self.service._state_repo.save_state(self.state)

        self.service._engine.execute_command("click 450 350")  # select white pawn
        self.service._engine.execute_command("click 350 250")  # click (2,3)

        self.service._engine.execute_command("wait 500")

        self.assertEqual(self.board.get_piece(Position(2, 3)), w_pawn)
        self.assertIsNone(self.board.get_piece(Position(3, 3)))

        # Captured black pawn's cooldown must be cleaned up to avoid stale state.
        self.state = self.service._state_repo.get_state()
        self.assertEqual(len(self.state.active_cooldowns), 1)  # Only white pawn in cooldown
        self.assertEqual(self.state.active_cooldowns[0].piece, w_pawn)

        # En-passant target should be consumed after the capture.
        self.assertEqual(len(self.state.en_passant_targets), 0)

    def test_en_passant_target_invalidated_if_pawn_moves_away(self) -> None:
        self._clear_board()
        w_pawn = TextPiece("w", "P")
        b_pawn = TextPiece("b", "P")

        self.board.set_piece(Position(3, 4), w_pawn)
        self.board.set_piece(Position(3, 3), b_pawn)

        ep_target = EnPassantTarget(pos=Position(2, 3), capture_pos=Position(3, 3), expires_ms=3000)
        self.state.en_passant_targets.append(ep_target)

        self.service._board_repo.save_board(self.board)
        self.service._state_repo.save_state(self.state)

        self.service._engine.execute_command("click 350 350")  # select black pawn
        self.service._engine.execute_command("click 350 450")  # move to (4,3)
        self.service._engine.execute_command("wait 1000")

        self.assertEqual(self.board.get_piece(Position(4, 3)), b_pawn)
        self.assertIsNone(self.board.get_piece(Position(3, 3)))

        self.service._engine.execute_command("click 450 350")  # select white pawn
        self.service._engine.execute_command("click 350 250")  # click (2,3)
        self.service._engine.execute_command("wait 1000")

        # En-passant must be invalid because the capturable pawn moved away.
        self.assertEqual(self.board.get_piece(Position(3, 4)), w_pawn)
        self.assertIsNone(self.board.get_piece(Position(2, 3)))

    # -------------------------------------------------------------------------
    # Pawn Promotion Tests
    # -------------------------------------------------------------------------

    def test_pawn_promotion_on_reaching_back_rank(self) -> None:
        self._clear_board()
        w_pawn = TextPiece("w", "P")
        self.board.set_piece(Position(1, 0), w_pawn)
        self.service._board_repo.save_board(self.board)

        self.service._engine.execute_command("click 50 150")  # select white pawn at (1,0)
        self.service._engine.execute_command("click 50 50")   # click (0,0)
        self.service._engine.execute_command("wait 1000")

        promoted = self.board.get_piece(Position(0, 0))
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted.piece_type, "Q")


if __name__ == "__main__":
    unittest.main()
