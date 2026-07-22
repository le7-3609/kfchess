"""Wire frame vocabulary — the `type` discriminator of every protocol message.

Layer: application (server/application/dtos)
Owns: the string constants naming each frame on the wire.
Must not own: frame construction (see response_frames), payload mapping (see
protocol_mapper), or any game state.

Request and response names live together rather than in separate modules
because the wire vocabulary is one shared namespace: `auth` is both the
client's handshake and the server's acknowledgement, and `ping`/`pong` are
only meaningful as a pair. Splitting by direction would put two halves of one
contract in two files and invite them to drift.
"""

MSG_MOVE = "move"
MSG_GAME_STATE = "game_state"
MSG_ERROR = "error"
MSG_AUTH = "auth"
MSG_PLAY = "play"
MSG_CANCEL_SEARCH = "cancel_search"
MSG_GAME_START = "game_start"
MSG_GAME_END = "game_end"
MSG_CREATE_ROOM = "create_room"
MSG_JOIN_ROOM = "join_room"
MSG_ROOM_CREATED = "room_created"
MSG_PING = "ping"
MSG_PONG = "pong"
MSG_OPPONENT_DISCONNECTED = "opponent_disconnected"
MSG_COUNTDOWN_TICK = "countdown_tick"
MSG_MATCH_TIMEOUT = "match_timeout"
MSG_RECONNECT = "reconnect"
MSG_INFO = "info"
MSG_OPPONENT_RECONNECTED = "opponent_reconnected"
MSG_FORFEIT_VICTORY = "forfeit_victory"

# EventBus broadcast frames — one per domain event fanned out to clients.
MSG_EVENT_GAME_STARTED = "event_game_started"
MSG_EVENT_MOVE_STARTED = "event_move_started"
MSG_EVENT_PIECE_MOVED = "event_piece_moved"
MSG_EVENT_PIECE_CAPTURED = "event_piece_captured"
MSG_EVENT_MOVE_ABORTED = "event_move_aborted"
MSG_EVENT_PIECE_PROMOTED = "event_piece_promoted"
MSG_EVENT_SCORE_UPDATED = "event_score_updated"
MSG_EVENT_GAME_ENDED = "event_game_ended"
