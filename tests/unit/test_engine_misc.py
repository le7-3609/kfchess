"""Unit tests for miscellaneous branches in GameEngine."""

import unittest
from kungfu_chess.bootstrap import build_service
from kungfu_chess.model.position import Position
from kungfu_chess.model.piece import TextPiece
from kungfu_chess.model.board import ArrayBoard
from kungfu_chess.model.game_state import GameState, Cooldown

class TestEngineMisc(unittest.TestCase):
    def setUp(self) -> None:
        self.service = build_service()
        self.board = ArrayBoard(8, 8)
        self.state = GameState()
        self.service._board_repo.save_board(self.board)
        self.service._state_repo.save_state(self.state)

    def test_click_during_game_over(self) -> None:
        self.state.game_over_reason = "checkmate"
        self.service._state_repo.save_state(self.state)
        # Should be ignored
        self.service._engine.execute_command("click 50 50")
        self.assertEqual(self.service._state_repo.get_state().selected_pos, None)

    def test_castling_wrong_pieces(self) -> None:
        k_pos = Position(7, 4)
        r_pos = Position(7, 7)
        self.board.set_piece(k_pos, TextPiece("w", "P"))  # Not a king
        self.board.set_piece(r_pos, TextPiece("w", "R"))
        self.service._board_repo.save_board(self.board)
        
        self.service._engine.execute_command("click 450 750")
        self.service._engine.execute_command("click 750 750")
        self.assertEqual(self.board.get_piece(Position(7, 6)), None)

    def test_castling_wrong_target(self) -> None:
        k_pos = Position(7, 4)
        r_pos = Position(7, 7)
        self.board.set_piece(k_pos, TextPiece("w", "K"))
        self.board.set_piece(r_pos, TextPiece("w", "P"))  # Not a rook
        self.service._board_repo.save_board(self.board)
        
        self.service._engine.execute_command("click 450 750")
        self.service._engine.execute_command("click 750 750")
        self.assertEqual(self.board.get_piece(Position(7, 6)), None)
        
    def test_castling_enemies(self) -> None:
        k_pos = Position(7, 4)
        r_pos = Position(7, 7)
        self.board.set_piece(k_pos, TextPiece("w", "K"))
        self.board.set_piece(r_pos, TextPiece("b", "R"))  # Enemy
        self.service._board_repo.save_board(self.board)
        
        self.service._engine.execute_command("click 450 750")
        self.service._engine.execute_command("click 750 750")
        self.assertEqual(self.board.get_piece(Position(7, 6)), None)

    def test_select_empty_square(self) -> None:
        self.service._engine.execute_command("click 50 50")
        self.assertEqual(self.service._state_repo.get_state().selected_pos, None)
        
    def test_click_cooldown_piece(self) -> None:
        p = TextPiece("w", "P")
        p.transition_to_cooldown()
        self.board.set_piece(Position(0, 0), p)
        self.state.active_cooldowns.append(Cooldown(p, 1000))
        self.service._board_repo.save_board(self.board)
        self.service._state_repo.save_state(self.state)
    
        self.service._engine.execute_command("click 50 50")
        self.assertEqual(self.service._state_repo.get_state().selected_pos, None)

    def test_click_moving_piece(self) -> None:
        p = TextPiece("w", "P")
        p.transition_to_moving()
        self.board.set_piece(Position(0, 0), p)
        self.service._board_repo.save_board(self.board)
        
        self.service._engine.execute_command("click 50 50")
        self.assertEqual(self.service._state_repo.get_state().selected_pos, None)
        
    def test_reselect_piece(self) -> None:
        self.board.set_piece(Position(0, 0), TextPiece("w", "R"))
        self.board.set_piece(Position(0, 1), TextPiece("w", "K"))
        self.service._board_repo.save_board(self.board)
        
        self.service._engine.execute_command("click 50 50")
        self.assertEqual(self.service._state_repo.get_state().selected_pos, Position(0, 0))
        self.service._engine.execute_command("click 150 50")
        self.assertEqual(self.service._state_repo.get_state().selected_pos, Position(0, 1))

    def test_deselect_piece(self) -> None:
        self.board.set_piece(Position(0, 0), TextPiece("w", "R"))
        self.service._board_repo.save_board(self.board)
        
        self.service._engine.execute_command("click 50 50")
        self.assertEqual(self.service._state_repo.get_state().selected_pos, Position(0, 0))
        self.service._engine.execute_command("click 50 50")
        self.assertEqual(self.service._state_repo.get_state().selected_pos, None)

if __name__ == "__main__":
    unittest.main()
