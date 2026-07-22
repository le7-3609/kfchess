"""Unit tests for bot move-selection strategies (policy layer only).

These exercise the pure choose_move contract with a hand-built board — no
engine, no arbiter — because a strategy must not do any legality math of its
own: it only scores the moves it is handed.
"""

import random

import pytest

from shared.config import consts
from shared.input.bot_strategy import GreedyCaptureStrategy, RandomMoveStrategy
from shared.model.board import ArrayBoard
from shared.model.game_state import GameState
from shared.model.piece import TextPiece
from shared.model.position import Position


def _board_with(pieces) -> ArrayBoard:
    board = ArrayBoard(consts.DEFAULT_BOARD_ROWS, consts.DEFAULT_BOARD_COLS)
    for pos, piece in pieces:
        board.set_piece(pos, piece)
    return board


class TestGreedyCaptureStrategy:
    def setup_method(self):
        random.seed(0)  # make the tie-break deterministic for the assertions

    def test_prefers_the_higher_value_capture(self):
        src = Position(4, 4)
        take_pawn = (src, Position(3, 4))
        take_queen = (src, Position(3, 5))
        board = _board_with([
            (take_pawn[1], TextPiece(consts.COLOR_BLACK, consts.PIECE_PAWN)),
            (take_queen[1], TextPiece(consts.COLOR_BLACK, consts.PIECE_QUEEN)),
        ])

        chosen = GreedyCaptureStrategy().choose_move([take_pawn, take_queen], board, GameState())

        assert chosen == take_queen

    def test_captures_the_king_above_all_material(self):
        src = Position(4, 4)
        take_queen = (src, Position(3, 5))
        take_king = (src, Position(3, 3))
        board = _board_with([
            (take_queen[1], TextPiece(consts.COLOR_BLACK, consts.PIECE_QUEEN)),
            (take_king[1], TextPiece(consts.COLOR_BLACK, consts.PIECE_KING)),
        ])

        chosen = GreedyCaptureStrategy().choose_move([take_queen, take_king], board, GameState())

        assert chosen == take_king

    def test_quiet_position_still_returns_a_legal_move(self):
        moves = [(Position(4, 4), Position(3, 4)), (Position(4, 4), Position(4, 5))]
        board = _board_with([])  # every target empty -> all scores zero

        chosen = GreedyCaptureStrategy().choose_move(moves, board, GameState())

        assert chosen in moves

    def test_ties_break_across_equal_captures(self):
        """Two captures of equal value must both be reachable, not one fixed pick."""
        src = Position(4, 4)
        left = (src, Position(3, 3))
        right = (src, Position(3, 5))
        board = _board_with([
            (left[1], TextPiece(consts.COLOR_BLACK, consts.PIECE_ROOK)),
            (right[1], TextPiece(consts.COLOR_BLACK, consts.PIECE_ROOK)),
        ])
        strategy = GreedyCaptureStrategy()

        seen = {strategy.choose_move([left, right], board, GameState()) for _ in range(50)}

        assert seen == {left, right}

    def test_no_legal_moves_returns_none(self):
        assert GreedyCaptureStrategy().choose_move([], _board_with([]), GameState()) is None


class TestRandomMoveStrategy:
    def test_returns_a_move_from_the_set(self):
        moves = [(Position(1, 1), Position(2, 1)), (Position(1, 1), Position(1, 2))]
        assert RandomMoveStrategy().choose_move(moves, _board_with([]), GameState()) in moves

    def test_no_legal_moves_returns_none(self):
        assert RandomMoveStrategy().choose_move([], _board_with([]), GameState()) is None
