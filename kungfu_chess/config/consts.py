
DEFAULT_BOARD_ROWS = 8
DEFAULT_BOARD_COLS = 8
DEFAULT_CELL_SIZE_PX = 100
DEFAULT_JUMP_DURATION_MS = 1000
DEFAULT_MS_PER_SQUARE = 1000
DEFAULT_COOLDOWN_DURATION_MS = 1000
DEFAULT_EN_PASSANT_DURATION_MS = 3000
DEFAULT_HALFMOVES_FOR_DRAW = 100
DEFAULT_REPETITIONS_FOR_DRAW = 3

SLIDING_TYPES = ("R", "B", "Q", "P")
JUMPER_PIECES = ("N",)
KING_PIECES = ("K",)
PAWN_PIECES = ("P",)
DEFAULT_PROMOTION_PIECE = "Q"

PLAYER_W_COLOR = "w"
PLAYER_W_FORWARD_DIR = -1
PLAYER_W_PAWN_START_ROWS = [6]
PLAYER_W_PAWN_PROMOTION_RANK = 0

PLAYER_B_COLOR = "b"
PLAYER_B_FORWARD_DIR = 1
PLAYER_B_PAWN_START_ROWS = [1]
PLAYER_B_PAWN_PROMOTION_RANK = 7

# The standard opening setup, in the BoardParser DSL. Saved games record only
# their moves, never the board they began from, so the replay has to assume
# this same setup — keep it the one definition both the live game and the
# replay read from.
STARTING_POSITION = """
Board:
bR bN bB bQ bK bB bN bR
bP bP bP bP bP bP bP bP
.  .  .  .  .  .  .  .
.  .  .  .  .  .  .  .
.  .  .  .  .  .  .  .
.  .  .  .  .  .  .  .
wP wP wP wP wP wP wP wP
wR wN wB wQ wK wB wN wR
"""
