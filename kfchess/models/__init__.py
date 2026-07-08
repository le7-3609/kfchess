"""
kfchess.models
~~~~~~~~~~~~~~
Pure domain types — no I/O, no services.
"""
from kfchess.models.piece import TextPiece as Piece, PieceFactory
from kfchess.models.board import ArrayBoard as Board, Position
from kfchess.models.interfaces import BoardInterface, PieceInterface
from kfchess.models.result import Result
from kfchess.models.game_state import GameState, Movement

__all__ = [
    'Color',
    'PieceType',
    'Piece',
    'Position',
    'Board',
    'Result',
    'GameState',
    'Movement',
]

