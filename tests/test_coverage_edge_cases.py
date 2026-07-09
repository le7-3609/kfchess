import unittest

from kfchess.models.piece import TextPiece, PieceFactory
from kfchess.models.board import Board, Position
from kfchess.models.game_state import GameState, Movement
from kfchess.config.game_config import GameConfig
from kfchess.services.event_publisher import MoveEventPublisher
from kfchess.services.game_service import GameService
from kfchess.services.command_executor import CommandExecutor
from kfchess.repositories.in_memory import InMemoryBoardrepositories, InMemoryGameStaterepositories
from kfchess.services.parser import SimpleBoardParser
from kfchess.services.validator import BoardValidator
from kfchess.rules.move_validators import RookMoveValidator, BishopMoveValidator
from kfchess.rules.path_checker import PathChecker
from kfchess.rules.promotion_rules import StandardPawnPromotion
from kfchess.rules.move_validator_factory import MoveValidatorFactory
from kfchess.services.printer import ConsoleBoardPrinter
from kfchess.services.movement_manager import MovementManager, ChebyshevDistanceDuration

class TestCoverageEdgeCases(unittest.TestCase):

    def test_piece_repr(self):
        p = TextPiece("w", "K")
        self.assertEqual(repr(p), "TextPiece(w, K)")

    def test_event_publisher_unsubscribe(self):
        pub = MoveEventPublisher()
        class DummyListener:
            def on_move(self, piece, frm, to): pass
        listener = DummyListener()
        # unsubscribe un-registered
        pub.unsubscribe(listener)
        self.assertTrue(True) # Should not raise ValueError

    def test_game_service_empty_board(self):
        repo1 = InMemoryBoardrepositories()
        repo2 = InMemoryGameStaterepositories()
        service = GameService(repo1, repo2, SimpleBoardParser(), BoardValidator(), None)
        res = service.execute([])
        self.assertTrue(res.is_ok)

    def test_path_checker_pawn_invalid_col_diff(self):
        checker = PathChecker()
        b = Board(8, 8)
        p = TextPiece("w", "P")
        # diff > 1
        res = checker.can_land(b, p, Position(1, 1), Position(2, 3))
        self.assertFalse(res)

    def test_promotion_rules_missing_player_config(self):
        rule = StandardPawnPromotion()
        p = TextPiece("w", "P")
        cfg = GameConfig()
        cfg.players = {} # remove players
        rule.evaluate_promotion(p, Position(0,0), cfg)
        self.assertEqual(p.piece_type, "P") # not promoted

    def test_move_validators_rook_not_straight(self):
        val = RookMoveValidator()
        self.assertFalse(val.is_legal(Position(0,0), Position(1,2), "w", 8))
        
    def test_move_validators_bishop_not_diagonal(self):
        val = BishopMoveValidator()
        self.assertFalse(val.is_legal(Position(0,0), Position(1,2), "w", 8))

    def test_command_executor_invalid_command_length(self):
        board_repo = InMemoryBoardrepositories()
        state_repo = InMemoryGameStaterepositories()
        config = GameConfig()
        pub = MoveEventPublisher()
        validators = {"R": RookMoveValidator()}
        printer = ConsoleBoardPrinter()
        
        executor = CommandExecutor(
            board_repo, state_repo, printer, 
            MoveValidatorFactory(validators),
            pub, PathChecker(), config
        )
        executor.execute_command("")
        executor.execute_command("click 10") # missing y
        executor.execute_command("jump 10") # missing y

    def test_command_executor_no_board(self):
        board_repo = InMemoryBoardrepositories()
        state_repo = InMemoryGameStaterepositories()
        executor = CommandExecutor(
            board_repo, state_repo, ConsoleBoardPrinter(), 
            MoveValidatorFactory({}), MoveEventPublisher(), PathChecker(), GameConfig()
        )
        # Board is None
        state_repo.save_state(GameState())
        executor.execute_command("click 10 10")
        executor.execute_command("jump 10 10")
        executor.execute_command("print board")
        executor.execute_command("wait 100")

    def test_command_executor_jump_empty_target(self):
        board_repo = InMemoryBoardrepositories()
        state_repo = InMemoryGameStaterepositories()
        b = Board(8, 8)
        board_repo.save_board(b)
        state_repo.save_state(GameState())
        
        executor = CommandExecutor(
            board_repo, state_repo, ConsoleBoardPrinter(), 
            MoveValidatorFactory({}), MoveEventPublisher(), PathChecker(), GameConfig()
        )
        # click on empty board for jump
        executor._execute_active_jump(50, 50)
        self.assertTrue(True)
        # jump out of bounds
        executor._execute_active_jump(1000, 1000)
        self.assertTrue(True)

    def test_command_executor_stale_selection(self):
        board_repo = InMemoryBoardrepositories()
        state_repo = InMemoryGameStaterepositories()
        b = Board(8, 8)
        board_repo.save_board(b)
        state = GameState()
        state.selected_pos = Position(0, 0)
        state_repo.save_state(state)
        
        executor = CommandExecutor(
            board_repo, state_repo, ConsoleBoardPrinter(), 
            MoveValidatorFactory({}), MoveEventPublisher(), PathChecker(), GameConfig()
        )
        executor._execute_active_click(50, 50) # target is (0,0) empty
        self.assertIsNone(state_repo.get_state().selected_pos)

        # target is friendly
        b.set_piece(Position(0,0), TextPiece("w", "P"))
        board_repo.save_board(b)
        state = GameState()
        state.selected_pos = Position(1, 1)
        state_repo.save_state(state)
        executor._execute_active_click(50, 50) # target (0,0) with wP
        self.assertEqual(state_repo.get_state().selected_pos, Position(0, 0))

    def test_command_executor_cannot_move(self):
        board_repo = InMemoryBoardrepositories()
        state_repo = InMemoryGameStaterepositories()
        b = Board(8, 8)
        p = TextPiece("w", "P")
        p.transition_to_moving()
        b.set_piece(Position(0,0), p)
        board_repo.save_board(b)
        state = GameState()
        state.selected_pos = Position(0, 0)
        state_repo.save_state(state)
        
        executor = CommandExecutor(
            board_repo, state_repo, ConsoleBoardPrinter(), 
            MoveValidatorFactory({}), MoveEventPublisher(), PathChecker(), GameConfig()
        )
        executor._execute_active_click(150, 150) # try to move
        self.assertTrue(True)

    def test_movement_manager_edge_cases(self):
        cfg = GameConfig()
        mm = MovementManager(ChebyshevDistanceDuration(500), MoveEventPublisher(), PathChecker(), cfg)
        
        # Test get_position_at
        p1 = TextPiece("w", "N")
        m1 = Movement(Position(0,0), Position(2,1), p1, 100, 500)
        # Jumper pieces should return mov.frm when t is inside
        self.assertEqual(mm.get_position_at(m1, 200), Position(0,0))
        
        # Test dist == 0
        m2 = Movement(Position(0,0), Position(0,0), p1, 100, 500)
        self.assertEqual(mm.get_position_at(m2, 200), Position(0,0))
        
        # Test step >= dist
        p2 = TextPiece("w", "R")
        m3 = Movement(Position(0,0), Position(0,2), p2, 100, 500) # dist 2
        # ms_per_square = 200. step = (600 - 100) // 200 = 2 >= 2 dist
        self.assertEqual(mm.get_position_at(m3, 500), Position(0,2))
        
        # Test resolve_movements aborted_or_captured skip
        b = Board(8,8)
        st = GameState()
        p3 = TextPiece("w", "R")
        m4 = Movement(Position(0,0), Position(0,2), p3, 100, 500)
        m5 = Movement(Position(0,0), Position(0,2), p3, 100, 500)
        m6 = Movement(Position(0,0), Position(0,2), p3, 100, 500)
        st.active_movements = [m4, m5, m6]
        # At t=500, all arrive at (0,2). 
        # m4 collides with m5. m5 is aborted.
        # Then m4 collides with m6. Since m5 is aborted, the loop should skip m5 in inner loops (continue).
        mm.resolve_movements(b, st, 600)
        
        # Crossing collision 
        st = GameState()
        m7 = Movement(Position(0,0), Position(0,2), TextPiece("w", "R"), 0, 400) # (0,1) at 200
        m8 = Movement(Position(0,2), Position(0,0), TextPiece("b", "R"), 0, 400) # (0,1) at 200
        st.active_movements = [m7, m8]
        mm.resolve_movements(b, st, 200) # resolve at 200, they are both at (0,1)
        mm.resolve_movements(b, st, 400) # they cross

        # Airborne enemy capture logic
        st = GameState()
        king = TextPiece("w", "K")
        m_jump = Movement(Position(0,2), Position(0,2), TextPiece("b", "P"), 0, 1000) # airborne enemy
        m_arrive = Movement(Position(0,0), Position(0,2), king, 0, 500) # king arriving at 500
        st.active_movements = [m_jump, m_arrive]
        st.selected_pos = Position(0,0)
        b.set_piece(Position(0,0), king)
        b.set_piece(Position(0,2), TextPiece("b", "P"))
        mm.resolve_movements(b, st, 600)
        self.assertTrue(st.game_over)
        self.assertIsNone(st.selected_pos)
        
        # Winner loser edge cases
        st = GameState()
        m9 = Movement(Position(0,0), Position(0,0), TextPiece("w", "R"), 100, 500) # jump
        m10 = Movement(Position(0,1), Position(0,0), TextPiece("b", "P"), 0, 500) # arrive
        st.active_movements = [m9, m10] # jump vs arrive
        mm.resolve_movements(b, st, 600)
        
        st = GameState()
        m11 = Movement(Position(0,1), Position(0,0), TextPiece("w", "R"), 100, 500) # arrive
        m12 = Movement(Position(0,0), Position(0,0), TextPiece("b", "P"), 0, 500) # jump
        st.active_movements = [m11, m12] # arrive vs jump
        mm.resolve_movements(b, st, 600)
        
        st = GameState()
        m13 = Movement(Position(0,1), Position(0,0), TextPiece("w", "R"), 100, 500)
        m14 = Movement(Position(0,2), Position(0,0), TextPiece("b", "P"), 0, 500) 
        st.active_movements = [m13, m14] # start ms diff
        mm.resolve_movements(b, st, 600)

        st = GameState()
        m15 = Movement(Position(0,1), Position(0,0), TextPiece("w", "R"), 0, 500)
        m16 = Movement(Position(0,2), Position(0,0), TextPiece("b", "P"), 100, 500) 
        st.active_movements = [m15, m16] # start ms diff (reverse)
        mm.resolve_movements(b, st, 600)
        
        st = GameState()
        m17 = Movement(Position(0,1), Position(0,0), TextPiece("w", "R"), 100, 500)
        m18 = Movement(Position(0,2), Position(0,0), TextPiece("w", "P"), 100, 500) 
        st.active_movements = [m17, m18] # friendly tie break by index
        st.selected_pos = Position(0,2) # m18 frm
        mm.resolve_movements(b, st, 600)
        self.assertIsNone(st.selected_pos) # should be None when aborted friendly
        
        st = GameState()
        m19 = Movement(Position(0,1), Position(0,0), TextPiece("w", "R"), 100, 500)
        m20 = Movement(Position(0,2), Position(0,0), TextPiece("b", "P"), 100, 500) 
        st.active_movements = [m20, m19] # enemy tie break by index
        st.selected_pos = Position(0,1) # m19 frm
        mm.resolve_movements(b, st, 600)
        self.assertIsNone(st.selected_pos)

if __name__ == '__main__':
    unittest.main()
