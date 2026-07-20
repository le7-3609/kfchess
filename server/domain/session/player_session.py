"""Player session domain entity — identity and connection lifecycle.

Layer: domain (server/domain/session)
Owns: session identity (user_id, username, elo), assigned seat color, and the
connection-state machine (CONNECTED / DISCONNECTED / RECONNECTING).
Must not own: socket I/O, message serialization, room assignment, or auth
password verification — see server/session.py, the transport-aware wrapper
that composes this entity.
"""

from enum import Enum
from typing import Optional

from shared.config import consts

_VALID_COLORS = frozenset({consts.COLOR_WHITE, consts.COLOR_BLACK})


class ConnectionState(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"


class PlayerSession:
    """A player's identity and connection state, independent of transport."""

    def __init__(self, username: str, user_id: int, elo: int = 1200) -> None:
        if not username or not username.strip():
            raise ValueError("username must not be empty")

        self._username = username
        self._user_id = user_id
        self._elo = elo
        self._connection_state = ConnectionState.CONNECTED
        self._assigned_color: Optional[str] = None

    @property
    def username(self) -> str:
        return self._username

    @property
    def user_id(self) -> int:
        return self._user_id

    @property
    def elo(self) -> int:
        return self._elo

    @elo.setter
    def elo(self, value: int) -> None:
        self._elo = value

    @property
    def connection_state(self) -> ConnectionState:
        return self._connection_state

    @property
    def color(self) -> Optional[str]:
        return self._assigned_color

    def assign_color(self, color: str) -> None:
        if color not in _VALID_COLORS:
            raise ValueError(f"Unknown seat color: {color!r}")
        self._assigned_color = color

    def reconnect(self) -> None:
        """Mark the session live again after a dropped connection.

        Reconnecting an already-connected session would mask a bug in the
        caller (a duplicate reconnect handshake, or a stale seat lookup), so
        this fails fast rather than silently no-opping.
        """
        if self._connection_state == ConnectionState.CONNECTED:
            raise ValueError("Cannot reconnect a session that is already connected")
        self._connection_state = ConnectionState.CONNECTED

    def disconnect(self) -> None:
        self._connection_state = ConnectionState.DISCONNECTED

    @property
    def is_connected(self) -> bool:
        return self._connection_state == ConnectionState.CONNECTED
