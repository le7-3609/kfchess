"""Remote seat — a player whose socket is held by another instance.

Layer: application (server/application)
Owns: presenting a player the room's owner cannot reach directly as an ordinary
seat, by turning `send` into a broker publish addressed to that player.
Must not own: identity or connection-state invariants — those live in
`server.domain.session.player_session.PlayerSession`, which this composes, for
exactly the same reason the WebSocket-backed `PlayerSession` composes it.

**Why this exists.** `GameRoom`, `DisconnectHandler` and `MatchmakingUseCase` all
speak to a seat through the same small contract: identity, seat colour,
connection state, and `send`. None of them should learn that a player might be
somewhere else — that knowledge would spread through every one of them and turn
a transport detail into a rule. So the transport is what changes shape, and the
seat contract does not.

**Why it composes the domain session rather than reimplementing it.** The seat
contract is not just `send`: the room assigns a colour, the disconnect handler
reads connection state, and the ELO settlement writes `.elo`. Re-typing those
here would be a second, drifting copy of a domain entity that already exists —
and the version that drifts is the one the fleet uses, since the single-process
path would keep exercising the original.

**Why the subject is keyed by player, not by replica.** Addressing
`gateway.<replica>.<username>` would mean every publisher had to know where the
player currently is, and would break the moment they reconnected elsewhere —
which is the thing Step 5's directory exists to make survivable.
"""

import logging
from typing import Any, Dict, Optional

from server.domain.coordination.broker import session_frames_subject
from server.domain.matchmaking.elo import DEFAULT_PLAYER_ELO
from server.domain.session.player_session import (
    ConnectionState,
    PlayerSession as DomainPlayerSession,
)

_LOGGER = logging.getLogger(__name__)


class RemoteSeat:
    """A seat whose frames leave through the broker instead of a socket."""

    def __init__(
        self,
        username: str,
        user_id: int,
        elo: int = DEFAULT_PLAYER_ELO,
        broker: Any = None,
        replica: str = "",
    ) -> None:
        self._domain = DomainPlayerSession(username=username, user_id=user_id, elo=elo)
        self._broker = broker
        self._subject = session_frames_subject(username)
        # Which instance held the socket when this seat was created. Advisory
        # only — used for logs and metrics, never for addressing, so a player who
        # reconnects elsewhere does not strand their own seat.
        self.replica = replica

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
    def websocket(self) -> None:
        """No socket here.

        Named because several collaborators probe for it — the heartbeat
        monitor, the close path, and the room manager deciding whether to
        subscribe this seat to a room's event stream locally. Answering None is
        the honest reply and keeps each of them from having to ask what kind of
        seat this is.
        """
        return None

    @property
    def color(self) -> Optional[str]:
        return self._domain.color

    def assign_color(self, color: str) -> None:
        self._domain.assign_color(color)

    def reconnect(self, new_websocket: Any = None) -> None:
        """Mark the seat live again.

        Takes and ignores a socket so the signature matches `PlayerSession`'s:
        the reconnect path hands one in without knowing which kind of seat it
        holds, and a remote seat's frames were never going to a socket anyway.
        """
        self._domain.reconnect()

    def disconnect(self) -> None:
        self._domain.disconnect()

    @property
    def connected(self) -> bool:
        """Whether frames for this player are still worth publishing.

        There is no socket to probe, so this is the domain state alone. The
        gateway that holds the real socket is what notices it closed, and it
        does so through its own session — this seat learns about it the same way
        every other participant does.
        """
        return self._domain.is_connected

    async def send(self, message: Dict[str, Any]) -> None:
        """Publish one frame to whichever gateway holds this player's socket.

        Silently drops when disconnected, matching `PlayerSession.send`: the
        disconnect countdown, not the send path, decides that a missing player
        forfeits.
        """
        if not self.connected or self._broker is None:
            return
        try:
            await self._broker.publish(self._subject, message)
        except Exception as exc:
            _LOGGER.warning("Frame to remote seat %s failed: %s", self.username, exc)

    def __repr__(self) -> str:
        return f"RemoteSeat({self.username!r} via {self._subject!r})"
