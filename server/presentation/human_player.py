"""WebSocket-backed implementation of the player seat contract.

Layer: presentation (server/presentation)
Owns: writing protocol frames to a human player's live socket.
Must not own: the seat contract itself or any game state — it implements
server.domain.player.player_interface.PlayerInterface.
"""

import json
import logging
from typing import Any, Dict

from server.domain.player.player_interface import PlayerInterface
from server.application.dtos.response_frames import build_game_state_message
from server.presentation.ws_connection import is_socket_open

_LOGGER = logging.getLogger(__name__)


class HumanPlayer(PlayerInterface):
    """A human player connected via WebSocket."""

    def __init__(self, websocket: Any, color: str, username: str) -> None:
        self._websocket = websocket
        self._color = color
        self._username = username

    @property
    def color(self) -> str:
        return self._color

    @property
    def username(self) -> str:
        return self._username

    @property
    def connected(self) -> bool:
        return is_socket_open(self._websocket)

    async def send_game_state(self, state_data: Dict[str, Any]) -> None:
        await self.send_message(build_game_state_message(state_data))

    async def send_message(self, message: Dict[str, Any]) -> None:
        if not self.connected:
            return
        try:
            payload = json.dumps(message)
            await self._websocket.send(payload)
        except Exception as exc:
            _LOGGER.warning("Failed to send message to player %s: %s", self._username, exc)
