"""Player seat abstraction and the automated player that fills one.

Layer: domain (server/domain/player)
Owns: the polymorphic seat contract a room talks to, and the bot adapter that
satisfies it without a socket.
Must not own: game rules, session management, authentication, or transport —
the WebSocket-backed implementation of this contract lives in
server/presentation/human_player.py, which depends on this module rather than
the other way round.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class PlayerInterface(ABC):
    """Polymorphic abstraction representing a player in a game (Human or Bot)."""

    @abstractmethod
    async def send_game_state(self, state_data: Dict[str, Any]) -> None:
        """Send game snapshot state update to the player."""

    @abstractmethod
    async def send_message(self, message: Dict[str, Any]) -> None:
        """Send a generic protocol message to the player."""

    @property
    @abstractmethod
    def color(self) -> str:
        """Player's assigned color ("w" or "b")."""

    @property
    @abstractmethod
    def username(self) -> str:
        """Player's display username."""

    @property
    @abstractmethod
    def connected(self) -> bool:
        """True if player is currently connected and reachable."""


DEFAULT_BOT_USERNAME = "KungFuBot"
DEFAULT_BOT_ELO = 1200


class BotPlayerAdapter(PlayerInterface):
    """Seats an automated player, wrapping the input source that picks its moves.

    Satisfies the same seat contract a `PlayerSession` does (`assign_color`,
    `elo`, `send`, `disconnect`) so `GameRoom` can seat, broadcast to, and score
    a bot without special-casing it anywhere on the room's hot path.

    Outbound payloads are consumed silently: the bot reads the position through
    its input source's repositories, not off the wire. The wrapped
    `RandomBotInputSource` only *produces* commands — driving it on a cadence is
    `BotDriver`'s job, since a real-time bot must move on its own clock rather
    than in reply to a frame.
    """

    def __init__(
        self,
        color: Optional[str] = None,
        username: str = DEFAULT_BOT_USERNAME,
        elo: int = DEFAULT_BOT_ELO,
        input_source: Optional[Any] = None,
    ) -> None:
        self._color = color
        self._username = username
        self._elo = elo
        self._input_source = input_source

    @property
    def is_bot(self) -> bool:
        """Discriminator letting a room skip persistence that only suits humans."""
        return True

    @property
    def color(self) -> Optional[str]:
        return self._color

    def assign_color(self, color: str) -> None:
        self._color = color

    @property
    def username(self) -> str:
        return self._username

    @property
    def elo(self) -> int:
        return self._elo

    @elo.setter
    def elo(self, value: int) -> None:
        self._elo = value

    @property
    def input_source(self) -> Optional[Any]:
        return self._input_source

    def attach_input_source(self, input_source: Any) -> None:
        """Bind the move producer, once the room's engine core exists to build it against."""
        self._input_source = input_source

    @property
    def connected(self) -> bool:
        return True

    def disconnect(self) -> None:
        """No-op: a bot has no socket to lose, so its seat never opens a countdown."""

    async def send(self, message: Dict[str, Any]) -> None:
        pass

    async def send_game_state(self, state_data: Dict[str, Any]) -> None:
        pass

    async def send_message(self, message: Dict[str, Any]) -> None:
        pass
