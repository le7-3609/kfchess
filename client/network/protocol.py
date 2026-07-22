"""Client-side wire protocol vocabulary (client layer).

Owns: the frame-type names, payload field keys, auth actions, and wire-only
reason codes the client speaks — one registry for every client module.
Must not own: frame construction, socket I/O, or GUI state.

This deliberately *mirrors* server/application/dtos (network_frames.py and
frame_fields.py) rather than importing it: the dependency rule is
`client -> shared <- server`, so the client may never import the server
package. Keeping the restatement in a single module means the protocol can
drift in at most one place per side.
"""

# --------------------------------------------------------------------------
# Frame types (the `type` discriminator)
# --------------------------------------------------------------------------
MSG_TYPE_AUTH = "auth"
MSG_TYPE_ERROR = "error"
MSG_TYPE_INFO = "info"
MSG_TYPE_PLAY = "play"
MSG_TYPE_MOVE = "move"
MSG_TYPE_CANCEL_SEARCH = "cancel_search"
MSG_TYPE_RECONNECT = "reconnect"
MSG_TYPE_GAME_STATE = "game_state"
MSG_TYPE_GAME_START = "game_start"
MSG_TYPE_GAME_END = "game_end"
MSG_TYPE_CREATE_ROOM = "create_room"
MSG_TYPE_JOIN_ROOM = "join_room"
MSG_TYPE_ROOM_CREATED = "room_created"
MSG_TYPE_OPPONENT_DISCONNECTED = "opponent_disconnected"
MSG_TYPE_COUNTDOWN_TICK = "countdown_tick"
MSG_TYPE_OPPONENT_RECONNECTED = "opponent_reconnected"
MSG_TYPE_FORFEIT_VICTORY = "forfeit_victory"
MSG_TYPE_EVENT_PIECE_MOVED = "event_piece_moved"
MSG_TYPE_EVENT_SCORE_UPDATED = "event_score_updated"
MSG_TYPE_EVENT_PIECE_CAPTURED = "event_piece_captured"

# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
AUTH_ACTION_LOGIN = "login"
AUTH_ACTION_REGISTER = "register"

# --------------------------------------------------------------------------
# Payload field keys
# --------------------------------------------------------------------------
FIELD_TYPE = "type"
FIELD_ACTION = "action"
FIELD_USERNAME = "username"
FIELD_PASSWORD = "password"
FIELD_STATUS = "status"
FIELD_MESSAGE = "message"
FIELD_ELO = "elo"

FIELD_COLOR = "color"
FIELD_OPPONENT = "opponent"
FIELD_ROOM_ID = "room_id"

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
FIELD_REASON = "reason"

FIELD_PIECE_TYPE = "piece_type"
FIELD_HAS_MOVED = "has_moved"
FIELD_CAN_SELECT = "can_select"
FIELD_CAN_MOVE = "can_move"
FIELD_STATE_ELAPSED_MS = "state_elapsed_ms"
FIELD_STATE_DURATION_MS = "state_duration_ms"
FIELD_START_MS = "start_ms"
FIELD_ARRIVAL_MS = "arrival_ms"

FIELD_AT_MS = "at_ms"
FIELD_POS = "pos"
FIELD_WHITE_SCORE = "white_score"
FIELD_BLACK_SCORE = "black_score"

FIELD_WHITE = "white"
FIELD_BLACK = "black"
FIELD_NEW_ELO = "new_elo"
FIELD_ELO_CHANGE = "elo_change"

FIELD_COUNTDOWN_SECONDS = "countdown_seconds"
FIELD_SECONDS_REMAINING = "seconds_remaining"

# Wire-only end reason produced by the server's disconnect flow.
GAME_END_REASON_DISCONNECTION_TIMEOUT = "disconnection_timeout"
