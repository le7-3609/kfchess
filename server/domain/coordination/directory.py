"""Fleet directory — who is seated where, and which instance owns which room.

Layer: domain (server/domain/coordination)
Owns: the two lookups that stop being answerable once there is more than one
replica — "which room holds this player's seat" and "which instance owns this
room" — declared as ports, plus the in-process implementations used when no
shared store is configured.
Must not own: the store itself (drivers live in
`server/infrastructure/coordination/`), room lifecycle (RoomManager), or
ownership *policy* (Step 7's lease manager decides who may take a room; this
only records the answer).

The ports are declared in the domain — the innermost layer that names these
concepts — so the dependency arrow stays application <- infrastructure and
inward: `RoomManager` depends on a contract, and a Redis driver satisfies it
from the outside without anything reaching in.

**Why a directory at all.** `RoomManager.find_room_by_username` scans every
local room looking for a disconnected seat. That is correct for one process and
meaningless for fifty: the room is very often not local, and scanning what *is*
local answers "no such seat" for a player who plainly has one. The directory
replaces the scan with a keyed lookup that any replica can answer.

**Why entries expire.** A crash leaves entries behind that no code path will
ever delete, so the store must forget them on its own. The TTL is sized to the
game's expected lifetime plus the reconnect countdown — long enough that a live
game's seat is never forgotten mid-play, short enough that a crashed replica's
rooms are not still claiming players minutes later.
"""

import time
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Tuple

# Games last 30 to 90 seconds and a disconnected player gets a countdown on top
# of that. Ten minutes is comfortably longer than both, and short enough that a
# crashed replica's leftovers are gone well inside a deploy cycle.
DEFAULT_ENTRY_TTL_SECONDS = 600


@dataclass(frozen=True)
class SeatLocation:
    """Where a player's seat currently is, in terms any replica can act on."""

    username: str
    room_id: str
    replica: str

    def is_on(self, replica: str) -> bool:
        return self.replica == replica


class SeatDirectory(Protocol):
    """Maps a player onto the room and replica holding their seat."""

    async def bind_seat(self, location: SeatLocation) -> None:
        """Record (or refresh) where *location.username* is seated."""

    async def find_seat(self, username: str) -> Optional[SeatLocation]:
        """Where this player is seated, from any replica, or None."""

    async def release_seat(self, username: str) -> None:
        """Forget this player's seat once it is genuinely gone."""


class RoomDirectory(Protocol):
    """Maps a room onto the instance responsible for computing it."""

    async def register_room(self, room_id: str, owner: str) -> None:
        """Record (or refresh) *owner* as the instance running *room_id*."""

    async def owner_of(self, room_id: str) -> Optional[str]:
        """Which instance runs *room_id*, or None if nobody claims it."""

    async def release_room(self, room_id: str) -> None:
        """Forget a room that has finished."""


class InProcessDirectory:
    """Both directories over plain dicts — the no-infrastructure default.

    Entries are expired lazily on read rather than by a sweeper: there is
    exactly one reader, the map dies with the process anyway, and a background
    task purely to delete keys nobody will look at again is cost with no payoff.
    """

    def __init__(
        self,
        replica_id: str = "",
        ttl_seconds: int = DEFAULT_ENTRY_TTL_SECONDS,
        time_fn=time.monotonic,
    ) -> None:
        self._replica_id = replica_id
        self._ttl_seconds = ttl_seconds
        self._time_fn = time_fn
        self._seats: Dict[str, Tuple[SeatLocation, float]] = {}
        self._rooms: Dict[str, Tuple[str, float]] = {}

    @property
    def replica_id(self) -> str:
        return self._replica_id

    async def bind_seat(self, location: SeatLocation) -> None:
        self._seats[location.username] = (location, self._expiry())

    async def find_seat(self, username: str) -> Optional[SeatLocation]:
        entry = self._seats.get(username)
        if entry is None:
            return None
        location, expires_at = entry
        if self._time_fn() >= expires_at:
            del self._seats[username]
            return None
        return location

    async def release_seat(self, username: str) -> None:
        self._seats.pop(username, None)

    async def register_room(self, room_id: str, owner: str) -> None:
        self._rooms[room_id] = (owner, self._expiry())

    async def owner_of(self, room_id: str) -> Optional[str]:
        entry = self._rooms.get(room_id)
        if entry is None:
            return None
        owner, expires_at = entry
        if self._time_fn() >= expires_at:
            del self._rooms[room_id]
            return None
        return owner

    async def release_room(self, room_id: str) -> None:
        self._rooms.pop(room_id, None)

    def _expiry(self) -> float:
        return self._time_fn() + self._ttl_seconds
