"""Unit test ensuring bot moves preserve human player piece selection."""

from shared.bot_factory import build_bot_service
from shared.config import consts
from shared.model.board import ArrayBoard
from shared.model.game_state import GameState
from shared.model.piece import TextPiece
from shared.model.position import Position


def test_bot_move_preserves_human_selection():
    # Build bot service where human is White and bot is Black
    service = build_bot_service(bot_color=consts.COLOR_BLACK)

    board = ArrayBoard(8, 8)
    board.set_piece(Position(6, 4), TextPiece("w", "P"))
    board.set_piece(Position(1, 4), TextPiece("b", "P"))
    board.set_piece(Position(0, 4), TextPiece("b", "K"))
    board.set_piece(Position(7, 4), TextPiece("w", "K"))
    service._board_repo.save_board(board)
    service._state_repo.save_state(GameState())

    # Human selects White Pawn at (6, 4) (cell row 6, col 4)
    service.click(6, 4)

    state = service._state_repo.get_state()
    # The human selection must remain Position(6, 4) even if the bot made a move in reaction
    assert state.selected_pos == Position(6, 4)

