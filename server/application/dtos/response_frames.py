"""Outbound frame builders — constructs the JSON-friendly dicts sent to clients.

Layer: application (server/application/dtos)
Owns: the shape of every server-to-client frame.
Must not own: socket I/O, game state, or coordinate translation — callers pass
in already-mapped values.
"""

from typing import Any, Dict, Optional

from server.application.dtos.network_frames import (
    MSG_AUTH,
    MSG_ERROR,
    MSG_GAME_START,
    MSG_GAME_STATE,
    MSG_MOVE,
    MSG_ROOM_CREATED,
)


def build_move_message(from_sq: str, to_sq: str) -> Dict[str, Any]:
    return {"type": MSG_MOVE, "from": from_sq, "to": to_sq}


def build_game_state_message(snapshot_data: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": MSG_GAME_STATE, "state": snapshot_data}


def build_error_message(message: str) -> Dict[str, Any]:
    return {"type": MSG_ERROR, "message": message}


def build_game_start_message(
    color: str, opponent_name: str, room_id: Optional[str] = None
) -> Dict[str, Any]:
    """Build the frame announcing a seated game.

    `room_id` is omitted entirely when absent rather than sent as null, so
    clients can treat its presence as "this seat belongs to a named room".
    """
    message: Dict[str, Any] = {"type": MSG_GAME_START, "color": color, "opponent": opponent_name}
    if room_id is not None:
        message["room_id"] = room_id
    return message


def build_room_created_message(room_id: str) -> Dict[str, Any]:
    return {"type": MSG_ROOM_CREATED, "room_id": room_id}


def build_auth_success_message(username: str, elo: int) -> Dict[str, Any]:
    return {"type": MSG_AUTH, "status": "ok", "username": username, "elo": elo}
