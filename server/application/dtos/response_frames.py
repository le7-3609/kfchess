"""Outbound frame builders — constructs the JSON-friendly dicts sent to clients.

Layer: application (server/application/dtos)
Owns: the shape of every server-to-client frame.
Must not own: socket I/O, game state, or coordinate translation — callers pass
in already-mapped values.
"""

from typing import Any, Dict, Optional

from server.application.dtos.frame_fields import (
    FIELD_BLACK,
    FIELD_COLOR,
    FIELD_ELO,
    FIELD_FROM,
    FIELD_MESSAGE,
    FIELD_OPPONENT,
    FIELD_REASON,
    FIELD_ROOM_ID,
    FIELD_STATE,
    FIELD_STATUS,
    FIELD_TO,
    FIELD_TYPE,
    FIELD_USERNAME,
    FIELD_WHITE,
    FIELD_WINNER,
    STATUS_OK,
)
from server.application.dtos.network_frames import (
    MSG_AUTH,
    MSG_ERROR,
    MSG_GAME_END,
    MSG_GAME_START,
    MSG_GAME_STATE,
    MSG_MOVE,
    MSG_ROOM_CREATED,
)


def build_move_message(from_sq: str, to_sq: str) -> Dict[str, Any]:
    return {FIELD_TYPE: MSG_MOVE, FIELD_FROM: from_sq, FIELD_TO: to_sq}


def build_game_state_message(snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
    return {FIELD_TYPE: MSG_GAME_STATE, FIELD_STATE: snapshot_data}


def build_error_message(message: str) -> Dict[str, Any]:
    return {FIELD_TYPE: MSG_ERROR, FIELD_MESSAGE: message}


def build_game_start_message(
    color: str, opponent_name: str, room_id: Optional[str] = None
) -> Dict[str, Any]:
    """Build the frame announcing a seated game.

    `room_id` is omitted entirely when absent rather than sent as null, so
    clients can treat its presence as "this seat belongs to a named room".
    """
    message: Dict[str, Any] = {FIELD_TYPE: MSG_GAME_START, FIELD_COLOR: color, FIELD_OPPONENT: opponent_name}
    if room_id is not None:
        message[FIELD_ROOM_ID] = room_id
    return message


def build_room_created_message(room_id: str) -> Dict[str, Any]:
    return {FIELD_TYPE: MSG_ROOM_CREATED, FIELD_ROOM_ID: room_id}


def build_auth_success_message(username: str, elo: int) -> Dict[str, Any]:
    return {FIELD_TYPE: MSG_AUTH, FIELD_STATUS: STATUS_OK, FIELD_USERNAME: username, FIELD_ELO: elo}


def build_game_ended_message(
    reason: str,
    winner: Optional[str],
    white_rating: Optional[Dict[str, int]] = None,
    black_rating: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Build the frame announcing a finished game, with each side's rating change.

    `white_rating`/`black_rating` are each a `{"new_elo": int, "elo_change": int}`
    pair, omitted entirely (rather than sent as null) when the game was not
    rated — a bot opponent, or no database configured — so clients can treat
    their presence as "this game affected your rating".
    """
    message: Dict[str, Any] = {FIELD_TYPE: MSG_GAME_END, FIELD_REASON: reason, FIELD_WINNER: winner}
    if white_rating is not None:
        message[FIELD_WHITE] = white_rating
    if black_rating is not None:
        message[FIELD_BLACK] = black_rating
    return message
