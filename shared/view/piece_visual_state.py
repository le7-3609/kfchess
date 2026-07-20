"""Piece visual (animation) state — a rendering-only concept (Layer 6).

Owns: which sprite-sheet animation a piece should currently play.
Must not own: game rules, board mutation, timing advancement, or piece
lifecycle state (shared.model.piece.PieceStateInterface owns that;
this enum is derived from it plus arbiter/cooldown data for drawing only).
"""

from enum import Enum, auto


class PieceVisualState(Enum):
    IDLE = auto()
    MOVE = auto()
    JUMP = auto()
    SHORT_REST = auto()
    LONG_REST = auto()


# State table — how SnapshotBuilder derives PieceVisualState each frame from
# model.piece.PieceStateInterface + arbiter movement/cooldown data. This enum
# has no transition methods of its own; it is recomputed from scratch every
# frame in shared.view.snapshot_builder, not advanced incrementally.
#
# model lifecycle state      | condition (per frame)                           | -> visual state
# ---------------------------+-------------------------------------------------+----------------
# IdleState                  | no active cooldown for this piece               | IDLE
# CooldownState              | active cooldown, piece not mid-arbiter-movement | SHORT_REST
# MovingState                | active arbiter movement, mov.frm != mov.to      | MOVE
# JumpingState               | active arbiter movement, mov.frm == mov.to      | JUMP
#                            | (re-click on an already-selected piece; a       |
#                            |  knight's ordinary hop still renders as MOVE)   |
# (unreachable currently)    | --                                              | LONG_REST
#
# LONG_REST has sprite assets on disk and is loaded by SpriteLibrary, but no
# code path in SnapshotBuilder currently emits it — cooldown is always mapped
# to SHORT_REST regardless of duration. Reserved for a future longer-cooldown
# distinction (e.g. post-capture recovery) if one is added.
