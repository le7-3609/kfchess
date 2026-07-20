"""Room role — the seat a participant holds within a game room.

Layer: domain (server/domain/room)
Owns: the enumeration of seat kinds and their wire-facing value strings.
Must not own: seat assignment policy — that lives in
server.domain.room.game_room.GameRoom.
"""

from enum import Enum


class RoomRole(Enum):
    WHITE_PLAYER = "w"
    BLACK_PLAYER = "b"
    VIEWER = "viewer"
