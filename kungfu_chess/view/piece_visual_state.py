"""Piece visual (animation) state — a rendering-only concept (Layer 6).

Owns: which sprite-sheet animation a piece should currently play.
Must not own: game rules, board mutation, timing advancement, or piece
lifecycle state (kungfu_chess.model.piece.PieceStateInterface owns that;
this enum is derived from it plus arbiter/cooldown data for drawing only).
"""

from enum import Enum, auto


class PieceVisualState(Enum):
    IDLE = auto()
    MOVE = auto()
    JUMP = auto()
    SHORT_REST = auto()
    LONG_REST = auto()
