"""Unit tests for castling and endgame logic in GameEngine."""

import unittest
from kungfu_chess.bootstrap import build_service
from kungfu_chess.engine.input_commands import ClickCommand, WaitCommand
from kungfu_chess.model.position import Position
from kungfu_chess.model.piece import TextPiece

from kungfu_chess.model.board import ArrayBoard
from kungfu_chess.model.game_state import GameState

class TestEngineCastlingEndgame(unittest.TestCase):
    
    def setUp(self) -> None:
        self.service = build_service()
        self.board = ArrayBoard(8, 8)
        self.state = GameState()
        self.service._board_repo.save_board(self.board)
        self.service._state_repo.save_state(self.state)
        
    def test_castling_kingside(self) -> None:
        for r in range(self.board.rows):
            for c in range(self.board.cols):
                self.board.set_piece(Position(r, c), None)
                
        k_pos = Position(7, 4)
        r_pos = Position(7, 7)
        self.board.set_piece(k_pos, TextPiece("w", "K"))
        self.board.set_piece(r_pos, TextPiece("w", "R"))
        self.service._board_repo.save_board(self.board)
        
        self.service._engine.execute_command(ClickCommand(450, 750))  # select King at (7,4)
        self.service._engine.execute_command(ClickCommand(750, 750))  # click Rook at (7,7)
        
        # With instant movement: King goes to 7,6 and Rook goes to 7,5.
        self.assertEqual(self.board.get_piece(Position(7, 6)).piece_type, "K")
        self.assertEqual(self.board.get_piece(Position(7, 5)).piece_type, "R")

    def test_castling_blocked(self) -> None:
        for r in range(self.board.rows):
            for c in range(self.board.cols):
                self.board.set_piece(Position(r, c), None)
                
        # Bishop at (7,5) blocks the castling corridor.
        k_pos = Position(7, 4)
        b_pos = Position(7, 5)
        r_pos = Position(7, 7)
        self.board.set_piece(k_pos, TextPiece("w", "K"))
        self.board.set_piece(b_pos, TextPiece("w", "B"))
        self.board.set_piece(r_pos, TextPiece("w", "R"))
        self.service._board_repo.save_board(self.board)
        
        self.service._engine.execute_command(ClickCommand(450, 750))  # select King at (7,4)
        self.service._engine.execute_command(ClickCommand(750, 750))  # click Rook at (7,7)
        
        self.assertEqual(self.board.get_piece(Position(7, 4)).piece_type, "K")
        self.assertEqual(self.board.get_piece(Position(7, 7)).piece_type, "R")
        
    def test_castling_threatened(self) -> None:
        for r in range(self.board.rows):
            for c in range(self.board.cols):
                self.board.set_piece(Position(r, c), None)
                
        # Black rook at (0,5) threatens the castling corridor — castling is illegal.
        k_pos = Position(7, 4)
        r_pos = Position(7, 7)
        threat_pos = Position(0, 5)
        self.board.set_piece(k_pos, TextPiece("w", "K"))
        self.board.set_piece(r_pos, TextPiece("w", "R"))
        self.board.set_piece(threat_pos, TextPiece("b", "R"))
        self.service._board_repo.save_board(self.board)
        
        self.service._engine.execute_command(ClickCommand(450, 750))  # select King at (7,4)
        self.service._engine.execute_command(ClickCommand(750, 750))  # click Rook at (7,7)
        
        self.assertEqual(self.board.get_piece(Position(7, 4)).piece_type, "K")

    def test_checkmate(self) -> None:
        self.board.set_piece(Position(0, 4), TextPiece("b", "K"))
        self.board.set_piece(Position(1, 3), TextPiece("b", "P"))
        self.board.set_piece(Position(1, 5), TextPiece("b", "P"))
    
        self.board.set_piece(Position(1, 4), TextPiece("w", "Q"))
        self.board.set_piece(Position(2, 4), TextPiece("w", "R"))
        self.board.set_piece(Position(7, 4), TextPiece("w", "K"))
        self.service._board_repo.save_board(self.board)
    
        self.service._engine.execute_command(WaitCommand(100))
    
        state = self.service._state_repo.get_state()
        self.assertEqual(state.game_over_reason, "checkmate")

if __name__ == "__main__":
    unittest.main()
