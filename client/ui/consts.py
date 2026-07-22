"""UI constant registry (Layer 6).

Owns: every literal the presentation layer would otherwise spell out inline —
window geometry and tick rate, palette and font sizes, sprite-sheet loading
keys, replay-window padding, and the user-preference file's keys and defaults.
Must not own: behaviour of any kind, and no value the simulation depends on.
Rules, realtime, and engine must never import this module; if a constant is
needed below Layer 6 it belongs in `config/consts.py` instead.

Like `config/consts.py` this module holds no functions and no classes. It does
import the domain codes it labels (colors, game-over reasons) so display names
stay keyed to the real values rather than restating them as strings.
"""

from shared.config.consts import (
    COLOR_BLACK,
    COLOR_WHITE,
    GAME_OVER_CHECKMATE,
    GAME_OVER_FIFTY_MOVE_RULE,
    GAME_OVER_INSUFFICIENT_MATERIAL,
    GAME_OVER_KING_CAPTURED,
    GAME_OVER_STALEMATE,
    GAME_OVER_THREEFOLD_REPETITION,
)

# --------------------------------------------------------------------------
# Window and tick loop
# --------------------------------------------------------------------------
TICK_MS = 16
BOARD_SIZE = 640
MIN_BOARD_DIMENSION_PX = 100

# How often the networked game window drains NetworkClient's message queue.
NETWORK_POLL_MS = 20

WINDOW_TITLE = "Kung Fu Chess"
DEFAULT_WHITE_NAME = "White"
DEFAULT_BLACK_NAME = "Black"

COLOR_DISPLAY_NAMES = {COLOR_WHITE: "White", COLOR_BLACK: "Black"}
COLOR_BANNER_NAMES = {COLOR_WHITE: "WHITE", COLOR_BLACK: "BLACK"}

SPEED_PRESETS_MS = {"Fast": 600, "Normal": 1000, "Slow": 1600}
COOLDOWN_PRESETS_MS = {"Fast": 600, "Normal": 1000, "Slow": 1600}

# How long the bot waits between moves (simulation-clock ms). Ordered slow to
# fast so the lobby renders the presets in that order.
BOT_SPEED_PRESETS_MS = {"Slow": 2000, "Normal": 1000, "Fast": 500, "Blitz": 250}
DEFAULT_BOT_SPEED_PRESET = "Normal"

CAPTURE_FLASH_MS = 450
CAPTURE_FLASH_COLOR = (255, 70, 40)
CAPTURE_FLASH_MAX_ALPHA = 170

# --------------------------------------------------------------------------
# Side panel
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
# Board renderer
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
# Sprite loading
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

# Contrasting outline halo composited behind each piece sprite.
SPRITE_OUTLINE_BLUR_RADIUS = 2
SPRITE_TRANSPARENT_RGBA = (0, 0, 0, 0)

# --------------------------------------------------------------------------
# Sound cues
# --------------------------------------------------------------------------
SOUNDS_DIR_NAME = "sounds"
SOUND_FILE_WIN = "tada.wav"
SOUND_FILE_LOSE = "fail.wav"

# --------------------------------------------------------------------------
# Replay window
# --------------------------------------------------------------------------
# Held after the final arrival so the last move can be seen landing and
# resting rather than the window freezing the instant it touches down.
REPLAY_END_PAD_MS = 1500
REPLAY_MIN_SCRUBBER_MS = 1

REPLAY_BUTTON_WIDTH = 8
REPLAY_TIME_LABEL_WIDTH = 10
REPLAY_PLAY_LABEL = "Play"
REPLAY_PAUSE_LABEL = "Pause"
REPLAY_RESTART_LABEL = "Restart"

# --------------------------------------------------------------------------
# Fonts and spacing (Tk windows)
# --------------------------------------------------------------------------
UI_FONT_FAMILY = "Helvetica"
FONT_WEIGHT_BOLD = "bold"
TITLE_FONT_SIZE = 20
BODY_FONT_SIZE = 11
ENTRY_FONT_SIZE = 12
SECTION_TITLE_FONT_SIZE = 12

# Spacing ladder (px) — Tk padding values pick from these steps so sibling
# widgets stay on one rhythm instead of each inventing its own gap.
SPACING_XS = 3
SPACING_SM = 5
SPACING_MD = 8
SPACING_LG = 10
SPACING_XL = 12
SPACING_XXL = 15
SPACING_TITLE = 18
SPACING_SECTION = 20

# --------------------------------------------------------------------------
# Lobby window and its dialogs
# --------------------------------------------------------------------------
LOBBY_GEOMETRY = "460x380"
MATCHMAKING_DIALOG_GEOMETRY = "340x160"
ROOM_DIALOG_GEOMETRY = "360x200"
OFFLINE_DIALOG_GEOMETRY = "420x440"

LOBBY_FRAME_PADDING = "20 20 20 20"
BOT_PANEL_PADDING = "12 8 12 12"
LOBBY_BUTTON_PAD_Y = 8
LOBBY_BUTTON_IPAD_Y = 6
EXIT_BUTTON_IPAD_Y = 4
ROOM_ENTRY_IPAD_X = 10
RADIO_LABEL_WIDTH = 10
RADIO_BUTTON_PAD_X = 4

MATCHMAKING_DIALOG_TITLE = "Matchmaking"
ROOM_DIALOG_TITLE = "Room"
OFFLINE_DIALOG_TITLE = "Offline Game"

# The lobby polls its inbound frame queue on the Tk loop at this beat.
LOBBY_POLL_INTERVAL_MS = 200
# Client-side fallback: give up searching if the server never answers.
MATCHMAKING_TIMEOUT_SECONDS = 60.0

# Tk protocol/window-manager hooks and widget states.
WM_DELETE_WINDOW_PROTOCOL = "WM_DELETE_WINDOW"
WIDGET_STATE_DISABLED = "disabled"
DISABLED_TEXT_COLOR = "gray"

# Tk event binding sequences.
EVENT_CONFIGURE = "<Configure>"
EVENT_LEFT_CLICK = "<Button-1>"
EVENT_RIGHT_CLICK = "<Button-3>"
EVENT_DOUBLE_CLICK = "<Double-Button-1>"
ANCHOR_NORTH_WEST = "nw"
ANCHOR_CENTER = "center"

# The board sits between a left and a right side panel.
SIDE_PANEL_COUNT = 2
# Halving divisor used when centering a smaller box inside a larger one.
CENTERING_DIVISOR = 2

# Game window menu labels.
MENU_LABEL_GAME = "Game"
MENU_LABEL_SETTINGS = "Settings"

# --------------------------------------------------------------------------
# History picker dialog
# --------------------------------------------------------------------------
HISTORY_PICKER_TITLE = "Load History"
HISTORY_LISTBOX_WIDTH = 50
HISTORY_LISTBOX_MAX_ROWS = 15

# --------------------------------------------------------------------------
# User preferences file
# --------------------------------------------------------------------------
USER_SETTINGS_FILE_NAME = "user_settings.json"
DEFAULT_SPEED_LEVEL_MS = 1000
DEFAULT_COOLDOWN_LEVEL_MS = 50000

SETTINGS_KEY_PIECE_THEME = "pieceTheme"
SETTINGS_KEY_BOARD_THEME = "boardTheme"
SETTINGS_KEY_SPEED_LEVEL_MS = "speedLevelMs"
SETTINGS_KEY_COOLDOWN_LEVEL_MS = "cooldownLevelMs"
