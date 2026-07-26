"""Central constant registry (Layer 1).

Owns: every literal value the simulation would otherwise spell out inline —
piece and color codes, DSL keywords, game-over reasons, and saved-game
persistence keys.
Must not own: behaviour of any kind, or anything only the presentation layer
reads — palette, geometry, sprite-sheet keys and user preferences live in
`ui/consts.py` so a headless build never pulls them in. There are no
functions, no classes, and no imports here.

That last point is what makes a single shared module legal under the layering
rules: consts imports nothing, so it sits beneath every layer and any module
depending on it is still pointing inward. Values are grouped by the layer that
consumes them, so a section can be split into its own module later without
touching the values themselves.
"""

# --------------------------------------------------------------------------
# Piece types
# --------------------------------------------------------------------------
PIECE_KING = "K"
PIECE_QUEEN = "Q"
PIECE_ROOK = "R"
PIECE_BISHOP = "B"
PIECE_KNIGHT = "N"
PIECE_PAWN = "P"

ALL_PIECE_TYPES = (PIECE_KING, PIECE_QUEEN, PIECE_ROOK, PIECE_BISHOP, PIECE_KNIGHT, PIECE_PAWN)

SLIDING_TYPES = (PIECE_ROOK, PIECE_BISHOP, PIECE_QUEEN, PIECE_PAWN)
JUMPER_PIECES = (PIECE_KNIGHT,)
KING_PIECES = (PIECE_KING,)
PAWN_PIECES = (PIECE_PAWN,)
DEFAULT_PROMOTION_PIECE = PIECE_QUEEN

# The King+Rook pair whose legs cross mid-castle; both CollisionResolver and
# ArrivalResolver exempt exactly this set from the normal same-color rules.
CASTLING_PIECE_PAIR = {PIECE_KING, PIECE_ROOK}

# --------------------------------------------------------------------------
# Colors
# --------------------------------------------------------------------------
COLOR_WHITE = "w"
COLOR_BLACK = "b"
ALL_COLORS = (COLOR_WHITE, COLOR_BLACK)


def opponent_color(color: str) -> str:
    """The other seat's colour — the seat pairing is written down only here."""
    return COLOR_BLACK if color == COLOR_WHITE else COLOR_WHITE

# --------------------------------------------------------------------------
# Board geometry and player setup defaults
# --------------------------------------------------------------------------
DEFAULT_BOARD_ROWS = 8
DEFAULT_BOARD_COLS = 8
DEFAULT_CELL_SIZE_PX = 100
DEFAULT_JUMP_DURATION_MS = 1000
DEFAULT_MS_PER_SQUARE = 1000
DEFAULT_COOLDOWN_DURATION_MS = 1000
DEFAULT_EN_PASSANT_DURATION_MS = 3000
DEFAULT_HALFMOVES_FOR_DRAW = 100
DEFAULT_REPETITIONS_FOR_DRAW = 3

PLAYER_W_COLOR = COLOR_WHITE
PLAYER_W_FORWARD_DIR = -1
PLAYER_W_PAWN_START_ROWS = [6]
PLAYER_W_PAWN_PROMOTION_RANK = 0

PLAYER_B_COLOR = COLOR_BLACK
PLAYER_B_FORWARD_DIR = 1
PLAYER_B_PAWN_START_ROWS = [1]
PLAYER_B_PAWN_PROMOTION_RANK = 7

# Pawn rows on a board that is not the standard height: the back rank is
# derived from the board itself rather than assumed (see
# GameService._adjust_pawn_rules_for_board_height).
FIRST_ROW_INDEX = 0
SHORT_BOARD_B_PAWN_START_ROWS = [0]
SHORT_BOARD_B_PROMOTION_RANK_OFFSET = 1
SHORT_BOARD_W_PAWN_START_ROW_OFFSET = 1

# --------------------------------------------------------------------------
# Movement rules
# --------------------------------------------------------------------------
PAWN_DOUBLE_STEP = 2
PAWN_CAPTURE_COL_OFFSETS = (-1, 1)
PAWN_FORWARD_COL_DIFF = 0
PAWN_DIAGONAL_COL_DIFF = 1

CASTLE_KING_COL_STEPS = 2
CASTLE_ROOK_COL_STEPS = 1

# A move that covers more than one square passes through intermediate squares
# the arbiter must sample; a single-square move has none.
MIN_DISTANCE_WITH_INTERMEDIATE_STEPS = 1
MIN_MS_PER_SQUARE = 1
INSTANT_DURATION_MS = 0

# --------------------------------------------------------------------------
# Endgame evaluation
# --------------------------------------------------------------------------
# Any of these on the board means at least one side can still force mate, so
# the insufficient-material draw cannot apply.
MATING_MATERIAL_TYPES = (PIECE_PAWN, PIECE_ROOK, PIECE_QUEEN)

NO_NON_KING_PIECES = 0
LONE_MINOR_PIECE_COUNT = 1
ONE_MINOR_PIECE_EACH_COUNT = 2
# Two bishops draw only when they share a square color, which is parity of
# (row + col).
SQUARE_COLOR_MODULUS = 2

# --------------------------------------------------------------------------
# Game-over reasons (published on GameEndedEvent, stored on GameState)
# --------------------------------------------------------------------------
GAME_OVER_KING_CAPTURED = "king_captured"
GAME_OVER_CHECKMATE = "checkmate"
GAME_OVER_STALEMATE = "stalemate"
GAME_OVER_INSUFFICIENT_MATERIAL = "insufficient_material"
GAME_OVER_THREEFOLD_REPETITION = "threefold_repetition"
GAME_OVER_FIFTY_MOVE_RULE = "fifty_move_rule"

# --------------------------------------------------------------------------
# Move validation verdicts (carried by MoveValidation.reason)
# --------------------------------------------------------------------------
MOVE_OK = "ok"
MOVE_REJECT_OUTSIDE_BOARD = "outside_board"
MOVE_REJECT_EMPTY_SOURCE = "empty_source"
MOVE_REJECT_FRIENDLY_DESTINATION = "friendly_destination"
MOVE_REJECT_ILLEGAL_PIECE_MOVE = "illegal_piece_move"

# --------------------------------------------------------------------------
# Move-abort reasons (carried by MoveAbortedEvent)
# --------------------------------------------------------------------------
ABORT_REASON_FRIENDLY_COLLISION = "friendly_collision"
ABORT_REASON_PATH_BLOCKED = "path_blocked"
ABORT_REASON_CAPTURED_IN_FLIGHT = "captured_in_flight"

# --------------------------------------------------------------------------
# Material scoring
# --------------------------------------------------------------------------
# The king is worth nothing because capturing it ends the game outright — it
# never contributes to a material total.
PIECE_VALUES = {
    PIECE_PAWN: 1,
    PIECE_KNIGHT: 3,
    PIECE_BISHOP: 3,
    PIECE_ROOK: 5,
    PIECE_QUEEN: 9,
    PIECE_KING: 0,
}
STARTING_SCORE = 0

# --------------------------------------------------------------------------
# Bot behaviour
# --------------------------------------------------------------------------
# How long a paced bot waits between moves, on the simulation clock, when no
# profile overrides it. The pacer reads the game clock, never wall time.
DEFAULT_BOT_MOVE_INTERVAL_MS = 1000
# A greedy bot values capturing the enemy king above any material gain, since
# that capture wins the game outright (PIECE_VALUES scores the king at 0 for
# material totals, which is the opposite of what move selection wants).
BOT_KING_CAPTURE_VALUE = 1000
# When a paced bot's turn comes up but it produces no command — an LLM reply
# still in flight, or a position with no legal move yet — re-asking on every
# advance_clock tick would rerun the legal-move scan inside the 16ms render
# budget. The pacer instead polls the wait at this coarser beat.
BOT_EMPTY_RETRY_MS = 250

# --------------------------------------------------------------------------
# Text DSL: board blocks, commands, tokens
# --------------------------------------------------------------------------
BOARD_SECTION_HEADER = "Board:"
COMMANDS_SECTION_HEADER = "Commands:"
EXPECTED_SECTION_HEADER = "Expected:"

COMMAND_CLICK = "click"
COMMAND_RIGHT_CLICK = "right_click"
COMMAND_WAIT = "wait"
COMMAND_MOVE = "move"
COMMAND_PRINT = "print"
PRINT_TARGET_BOARD = "board"
COMMAND_PRINT_BOARD = COMMAND_PRINT + " " + PRINT_TARGET_BOARD
COMMENT_LINE_PREFIX = "#"

CLICK_COMMAND_PART_COUNT = 3
WAIT_COMMAND_PART_COUNT = 2
CELL_COMMAND_ARG_COUNT = 2
WAIT_COMMAND_ARG_COUNT = 1

EMPTY_SQUARE_TOKEN = "."
PIECE_TOKEN_LENGTH = 2
TOKEN_COLOR_INDEX = 0
TOKEN_PIECE_TYPE_INDEX = 1
BOARD_TOKEN_SEPARATOR = " "
BOARD_ROW_SEPARATOR = "\n"

WHITE_KING_TOKEN = COLOR_WHITE + PIECE_KING
BLACK_KING_TOKEN = COLOR_BLACK + PIECE_KING

# --------------------------------------------------------------------------
# Board validation error codes
# --------------------------------------------------------------------------
ERROR_EMPTY_BOARD = "EMPTY_BOARD"
ERROR_ROW_WIDTH_MISMATCH = "ROW_WIDTH_MISMATCH"
ERROR_UNKNOWN_TOKEN = "UNKNOWN_TOKEN"
ERROR_INVALID_KING_COUNT = "INVALID_KING_COUNT"
ERROR_NO_BOARD_FOUND = "No board found in input"
ERROR_OUTPUT_PREFIX = "ERROR"

REQUIRED_KINGS_PER_COLOR = 1

# --------------------------------------------------------------------------
# Board-state serialization (threefold repetition keys)
# --------------------------------------------------------------------------
SERIALIZED_FIELD_SEPARATOR = "|"
SERIALIZED_ENTRY_SEPARATOR = ","
SERIALIZED_CASTLING_SEPARATOR = ";"

# --------------------------------------------------------------------------
# Algebraic notation (MovesLog)
# --------------------------------------------------------------------------
NOTATION_FILES = "abcdefgh"
NOTATION_RANKS = 8
NOTATION_PATTERN = r"^([KQRBNP])([a-h][1-8])-([a-h][1-8])$"
NOTATION_MOVE_SEPARATOR = "-"

# --------------------------------------------------------------------------
# Time formatting
# --------------------------------------------------------------------------
MS_PER_SECOND = 1000
SECONDS_PER_MINUTE = 60

# --------------------------------------------------------------------------
# Persistence — saved games
# --------------------------------------------------------------------------
SAVED_GAMES_DIR_NAME = "saved_games"
SAVE_FILE_EXTENSION = ".json"
SAVE_TIMESTAMP_FORMAT = "%Y-%m-%d_%H-%M-%S"
SAVE_NAME_SAFE_PATTERN = r"[^a-zA-Z0-9_-]+"
SAVE_NAME_REPLACEMENT = "_"
DEFAULT_SAVE_NAME = "game"

FILE_ENCODING = "utf-8"
FILE_MODE_READ = "r"
FILE_MODE_WRITE = "w"
FILE_MODE_APPEND = "a"
JSON_INDENT = 2
LINE_SEPARATOR = "\n"

SAVE_KEY_NAME = "saveName"
SAVE_KEY_WHITE_NAME = "whiteName"
SAVE_KEY_BLACK_NAME = "blackName"
SAVE_KEY_WINNER = "winner"
SAVE_KEY_SAVED_AT = "savedAt"
SAVE_KEY_SPEED_MS = "speedMs"
SAVE_KEY_COOLDOWN_MS = "cooldownMs"
SAVE_KEY_MOVES = "moves"
SAVE_KEY_MOVE_COLOR = "color"
SAVE_KEY_MOVE_NOTATION = "notation"
SAVE_KEY_MOVE_TIME = "time"

# --------------------------------------------------------------------------
# Starting position
# --------------------------------------------------------------------------
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
