"""Wire frame payload vocabulary — every JSON key a protocol frame may carry.

Layer: application (server/application/dtos)
Owns: the string constants naming each payload field, status value, and
wire-only reason code.
Must not own: frame construction (see response_frames), payload mapping (see
protocol_mapper), or any game state.

Like network_frames.py, keys for both directions live together because the
wire vocabulary is one shared namespace — `username` appears in the client's
auth request and the server's acknowledgement alike.
"""

# --------------------------------------------------------------------------
# Envelope
# --------------------------------------------------------------------------
FIELD_TYPE = "type"
FIELD_STATUS = "status"
FIELD_MESSAGE = "message"

STATUS_OK = "ok"

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
FIELD_ACTION = "action"
FIELD_USERNAME = "username"
FIELD_PASSWORD = "password"
FIELD_ELO = "elo"

# --------------------------------------------------------------------------
# Seating and rooms
# --------------------------------------------------------------------------
FIELD_COLOR = "color"
FIELD_OPPONENT = "opponent"
FIELD_ROOM_ID = "room_id"

# --------------------------------------------------------------------------
# Moves and snapshot state
# --------------------------------------------------------------------------
FIELD_FROM = "from"
FIELD_TO = "to"
FIELD_STATE = "state"
FIELD_ROWS = "rows"
FIELD_COLS = "cols"
FIELD_PIECES = "pieces"
FIELD_SELECTED_POS = "selected_pos"
FIELD_LEGAL_MOVE_TARGETS = "legal_move_targets"
FIELD_CASTLE_TARGETS = "castle_targets"
FIELD_ACTIVE_MOVEMENTS = "active_movements"
FIELD_COOLDOWN_POSITIONS = "cooldown_positions"
FIELD_CLOCK_MS = "clock_ms"
FIELD_GAME_OVER = "game_over"
FIELD_GAME_OVER_REASON = "game_over_reason"
FIELD_WINNER = "winner"

FIELD_PIECE_TYPE = "piece_type"
FIELD_HAS_MOVED = "has_moved"
FIELD_CAN_SELECT = "can_select"
FIELD_CAN_MOVE = "can_move"
FIELD_STATE_ELAPSED_MS = "state_elapsed_ms"
FIELD_STATE_DURATION_MS = "state_duration_ms"
FIELD_START_MS = "start_ms"
FIELD_ARRIVAL_MS = "arrival_ms"

# --------------------------------------------------------------------------
# Event broadcast frames
# --------------------------------------------------------------------------
FIELD_AT_MS = "at_ms"
FIELD_POS = "pos"
FIELD_WAS_CAPTURE = "was_capture"
FIELD_CAPTOR_COLOR = "captor_color"
FIELD_CAPTOR_PIECE_TYPE = "captor_piece_type"
FIELD_CAPTOR_FROM = "captor_from"
FIELD_CAPTOR_TO = "captor_to"
FIELD_STOPPED_AT = "stopped_at"
FIELD_FROM_PIECE_TYPE = "from_piece_type"
FIELD_TO_PIECE_TYPE = "to_piece_type"
FIELD_WHITE_SCORE = "white_score"
FIELD_BLACK_SCORE = "black_score"
FIELD_REASON = "reason"

# --------------------------------------------------------------------------
# Game end / rating
# --------------------------------------------------------------------------
FIELD_WHITE = "white"
FIELD_BLACK = "black"
FIELD_NEW_ELO = "new_elo"
FIELD_ELO_CHANGE = "elo_change"

# --------------------------------------------------------------------------
# Disconnection countdown
# --------------------------------------------------------------------------
FIELD_COUNTDOWN_SECONDS = "countdown_seconds"
FIELD_SECONDS_REMAINING = "seconds_remaining"

# Wire-only end reasons — produced by the disconnect flow, never by the rules.
GAME_END_REASON_DISCONNECTION_TIMEOUT = "disconnection_timeout"
FORFEIT_REASON_OPPONENT_TIMEOUT = "opponent_disconnected_timeout"
