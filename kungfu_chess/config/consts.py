"""Central constant registry (Layer 1).

Owns: every literal value the rest of the codebase would otherwise spell out
inline — piece and color codes, DSL keywords, game-over reasons, persistence
keys, and UI palette/geometry.
Must not own: behaviour of any kind. There are no functions, no classes, and
no imports here.

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
# Text DSL: board blocks, commands, tokens
# --------------------------------------------------------------------------
BOARD_SECTION_HEADER = "Board:"
COMMANDS_SECTION_HEADER = "Commands:"

COMMAND_CLICK = "click"
COMMAND_RIGHT_CLICK = "right_click"
COMMAND_WAIT = "wait"
COMMAND_PRINT_BOARD = "print board"

CLICK_COMMAND_PART_COUNT = 3
WAIT_COMMAND_PART_COUNT = 2

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
JSON_INDENT = 2

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
# Persistence — user settings
# --------------------------------------------------------------------------
USER_SETTINGS_FILE_NAME = "user_settings.json"
DEFAULT_SPEED_LEVEL_MS = 1000
DEFAULT_COOLDOWN_LEVEL_MS = 50000

SETTINGS_KEY_PIECE_THEME = "pieceTheme"
SETTINGS_KEY_BOARD_THEME = "boardTheme"
SETTINGS_KEY_SPEED_LEVEL_MS = "speedLevelMs"
SETTINGS_KEY_COOLDOWN_LEVEL_MS = "cooldownLevelMs"

# --------------------------------------------------------------------------
# UI — window and tick loop
# --------------------------------------------------------------------------
TICK_MS = 16
BOARD_SIZE = 640
MIN_BOARD_DIMENSION_PX = 100

WINDOW_TITLE = "Kung Fu Chess"
DEFAULT_WHITE_NAME = "White"
DEFAULT_BLACK_NAME = "Black"

COLOR_DISPLAY_NAMES = {COLOR_WHITE: "White", COLOR_BLACK: "Black"}
COLOR_BANNER_NAMES = {COLOR_WHITE: "WHITE", COLOR_BLACK: "BLACK"}

SPEED_PRESETS_MS = {"Fast": 600, "Normal": 1000, "Slow": 1600}
COOLDOWN_PRESETS_MS = {"Fast": 600, "Normal": 1000, "Slow": 1600}

CAPTURE_FLASH_MS = 450
CAPTURE_FLASH_COLOR = (255, 70, 40)
CAPTURE_FLASH_MAX_ALPHA = 170

# --------------------------------------------------------------------------
# UI — side panel
# --------------------------------------------------------------------------
PANEL_BACKGROUND_COLOR = (45, 45, 45, 255)
PANEL_TEXT_COLOR = (235, 235, 235, 255)
PANEL_SCORE_COLOR = (150, 220, 150, 255)

SIDE_PANEL_WIDTH = 220
PANEL_TOP_HEIGHT = 50
PANEL_ROW_HEIGHT = 20
PANEL_MAX_ROWS = 12

PANEL_ROW_TEXT_X_OFFSET = 10
PANEL_ROW_FONT_SIZE = 11
PANEL_NAME_FONT_SIZE = 16
PANEL_SCORE_FONT_SIZE = 12
PANEL_NAME_ONLY_Y = 12
PANEL_NAME_WITH_SCORE_Y = 6
PANEL_SCORE_Y = 28

TEXT_ANCHOR_LEFT_TOP = "lt"
TEXT_ANCHOR_MIDDLE_TOP = "mt"
TEXT_ANCHOR_MIDDLE_MIDDLE = "mm"

# --------------------------------------------------------------------------
# UI — board renderer
# --------------------------------------------------------------------------
LIGHT_SQUARE = (240, 217, 181, 255)
DARK_SQUARE = (181, 136, 99, 255)
BOARD_BACKDROP_COLOR = (30, 30, 30, 255)
SELECTION_COLOR = (220, 30, 30, 255)
LEGAL_MOVE_CAPTURE_COLOR = (20, 255, 47, 160)
LEGAL_MOVE_EMPTY_COLOR = (255, 246, 79, 160)
CASTLE_TARGET_COLOR = (240, 200, 40, 160)
JUMP_SHADOW_COLOR = (0, 0, 0, 90)
GAME_OVER_OVERLAY_COLOR = (0, 0, 0, 160)
GAME_OVER_TEXT_COLOR = (255, 255, 255, 255)

REST_RED = (219, 68, 55)
REST_AMBER = (240, 173, 40)
REST_GREEN = (52, 168, 83)
REST_MIDPOINT_FRACTION = 0.5
REST_FILL_ALPHA = 110

CHECKERBOARD_MODULUS = 2
SELECTION_INSET_PX = 2
SELECTION_BORDER_WIDTH = 4
LEGAL_MOVE_DOT_RADIUS_RATIO = 0.15
PIECE_SPRITE_SIZE_RATIO = 0.85

JUMP_SHADOW_WIDTH_RATIO = 0.7
JUMP_SHADOW_HEIGHT_RATIO = 0.18
JUMP_SHADOW_Y_RATIO = 0.82
JUMP_LIFT_RATIO = 0.4

GAME_OVER_LABELS = {
    GAME_OVER_KING_CAPTURED: "KING CAPTURED",
    GAME_OVER_CHECKMATE: "CHECKMATE",
    GAME_OVER_STALEMATE: "STALEMATE",
    GAME_OVER_INSUFFICIENT_MATERIAL: "DRAW - INSUFFICIENT MATERIAL",
    GAME_OVER_THREEFOLD_REPETITION: "DRAW - THREEFOLD REPETITION",
    GAME_OVER_FIFTY_MOVE_RULE: "DRAW - FIFTY MOVE RULE",
}
GAME_OVER_DEFAULT_LABEL = "GAME OVER"
GAME_OVER_WINNER_SUFFIX = "WINS"
GAME_OVER_DRAW_SUFFIX = "DRAW"

# Arial glyphs average about 0.6 of their point size in width, which is what
# lets the banner size itself to fit the board without measuring the string.
GAME_OVER_FONT_WIDTH_RATIO = 0.6
GAME_OVER_TEXT_WIDTH_FRACTION = 0.9
GAME_OVER_FONT_SIZE_DIVISOR = 16
GAME_OVER_MIN_FONT_SIZE = 10

# --------------------------------------------------------------------------
# UI — sprite loading
# --------------------------------------------------------------------------
ASSETS_DIR_NAME = "assets"
SPRITE_STATES_DIR = "states"
SPRITE_FRAMES_DIR = "sprites"
SPRITE_CONFIG_FILE = "config.json"
SPRITE_FRAME_EXTENSION = ".png"
SPRITE_FIRST_FRAME_INDEX = 1

SPRITE_FPS_PATTERN = r'"frames_per_sec"\s*:\s*(-?\d+)'
SPRITE_LOOP_PATTERN = r'"is_loop"\s*:\s*(true|false)'
SPRITE_JSON_TRUE = "true"
SPRITE_DEFAULT_FPS = 8
SPRITE_DEFAULT_IS_LOOP = True

IMAGE_MODE_RGBA = "RGBA"
IMAGE_MODE_RGB = "RGB"
IMAGE_MODE_LUMINANCE = "L"
IMAGE_CHANNEL_ALPHA = "A"

# Luminance is squeezed into a narrow band so every theme's art reads as a
# flat black or white silhouette regardless of its source palette.
SPRITE_DARK_LUMINANCE_FLOOR = 12
SPRITE_LIGHT_LUMINANCE_FLOOR = 205
SPRITE_LUMINANCE_SPAN = 55
LUMINANCE_MATRIX = (0.299, 0.587, 0.114, 0)
COLOR_CHANNEL_MIN = 0
COLOR_CHANNEL_MAX = 255
COLOR_CHANNEL_LEVELS = 256
RGB_CHANNEL_COUNT = 3

# --------------------------------------------------------------------------
# UI — replay window
# --------------------------------------------------------------------------
# Held after the final arrival so the last move can be seen landing and
# resting rather than the window freezing the instant it touches down.
REPLAY_END_PAD_MS = 1500
REPLAY_MIN_SCRUBBER_MS = 1

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
