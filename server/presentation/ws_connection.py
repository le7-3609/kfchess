"""Player session — transport-aware wrapper around the domain session.

Owns: the live WebSocket handle, socket liveness detection, and outbound JSON
encoding for a connected player.
Must not own: identity or connection-state invariants — those live in
server.domain.session.player_session.PlayerSession, which this class
composes.
"""

import json
import logging
from typing import Any, Dict, Optional

from server.domain.matchmaking.elo import DEFAULT_PLAYER_ELO
from server.domain.session.player_session import ConnectionState, PlayerSession as DomainPlayerSession

_LOGGER = logging.getLogger(__name__)

# Socket-liveness attributes probed across websockets library versions.
_ATTR_CLOSE_CODE = "close_code"
_ATTR_OPEN = "open"


def is_socket_open(websocket: Any) -> bool:
    """Report whether *websocket* can still be written to.

    websockets >= 14 dropped the `.open` attribute, so probing for it with a
    True default silently reports every socket as live — leaving the server
    broadcasting into closed connections and stalling each client's close
    handshake until its timeout. A socket that has begun closing always
    exposes a `close_code`, which is checked first; the `.open` fallback keeps
    older releases (and test doubles) working.
    """
    if websocket is None:
        return False
    if getattr(websocket, _ATTR_CLOSE_CODE, None) is not None:
        return False
    return getattr(websocket, _ATTR_OPEN, True)


class PlayerSession:
    """Represents a connected player's network and identity session."""

    def __init__(
        self,
        websocket: Any,
        username: str,
        user_id: int,
        elo: int = DEFAULT_PLAYER_ELO,
    ) -> None:
        self._websocket = websocket
        self._domain = DomainPlayerSession(username=username, user_id=user_id, elo=elo)

    @property
    def username(self) -> str:
        return self._domain.username

    @property
    def user_id(self) -> int:
        return self._domain.user_id

    @property
    def elo(self) -> int:
        return self._domain.elo

    @elo.setter
    def elo(self, value: int) -> None:
        self._domain.elo = value

    @property
    def connection_state(self) -> ConnectionState:
        return self._domain.connection_state

    @property
    def websocket(self) -> Any:
        return self._websocket

    @websocket.setter
    def websocket(self, ws: Any) -> None:
        self._websocket = ws

    @property
    def color(self) -> Optional[str]:
        return self._domain.color

    def assign_color(self, color: str) -> None:
        self._domain.assign_color(color)

    def reconnect(self, new_websocket: Any) -> None:
        self._websocket = new_websocket
        self._domain.reconnect()

    def disconnect(self) -> None:
        self._domain.disconnect()

    @property
    def connected(self) -> bool:
        if not self._domain.is_connected:
            return False
        return is_socket_open(self._websocket)

    async def send(self, message: Dict[str, Any]) -> None:
        if not self.connected:
            return
        try:
            payload = json.dumps(message)
            await self._websocket.send(payload)
        except Exception as exc:
            _LOGGER.warning("Failed to send to session %s: %s", self._domain.username, exc)
