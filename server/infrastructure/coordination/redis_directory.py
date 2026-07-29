"""Redis fleet directory — the seat and room maps, readable from any replica.

Owns: the two hash keys the directory lives in and their expiry.
Must not own: what a seat or a room means, or who is allowed to own one.

Each entry is its own key rather than a field in one big hash, because Redis
expires keys and not hash fields. A single `dir:seats` hash would need a
sweeper to delete the entries a crashed replica left behind; a key per seat
lets Redis forget them by itself, which is the whole reason the TTL exists.
"""

import json
import logging
from typing import Optional

from server.domain.coordination.directory import DEFAULT_ENTRY_TTL_SECONDS, SeatLocation
from server.infrastructure.coordination.redis_client import RedisConnection, RedisError

_LOGGER = logging.getLogger(__name__)

KEY_SEAT = "dir:seat"
KEY_ROOM = "dir:room"


class RedisDirectory:
    """Both directory ports over Redis keys with a matched TTL."""

    def __init__(
        self,
        connection: RedisConnection,
        replica_id: str,
        ttl_seconds: int = DEFAULT_ENTRY_TTL_SECONDS,
    ) -> None:
        self._connection = connection
        self._replica_id = replica_id
        self._ttl_seconds = ttl_seconds

    @property
    def replica_id(self) -> str:
        return self._replica_id

    async def bind_seat(self, location: SeatLocation) -> None:
        await self._set(
            self._connection.key(KEY_SEAT, location.username),
            json.dumps({"room_id": location.room_id, "replica": location.replica}),
        )

    async def find_seat(self, username: str) -> Optional[SeatLocation]:
        raw = await self._get(self._connection.key(KEY_SEAT, username))
        if raw is None:
            return None
        try:
            fields = json.loads(raw)
            return SeatLocation(
                username=username,
                room_id=fields["room_id"],
                replica=fields["replica"],
            )
        except (ValueError, KeyError, TypeError) as exc:
            _LOGGER.error("Discarding unreadable seat entry for %s: %s", username, exc)
            return None

    async def release_seat(self, username: str) -> None:
        await self._delete(self._connection.key(KEY_SEAT, username))

    async def register_room(self, room_id: str, owner: str) -> None:
        await self._set(self._connection.key(KEY_ROOM, room_id), owner)

    async def owner_of(self, room_id: str) -> Optional[str]:
        return await self._get(self._connection.key(KEY_ROOM, room_id))

    async def release_room(self, room_id: str) -> None:
        await self._delete(self._connection.key(KEY_ROOM, room_id))

    async def _set(self, key: str, value: str) -> None:
        """Write with the directory TTL, tolerating an unreachable Redis.

        A failed directory write is degraded routing, not a failed game: the
        room is already running locally and the player is already seated. Raising
        here would turn a Redis blip into a dropped connection.
        """
        try:
            await self._connection.client.set(key, value, ex=self._ttl_seconds)
        except (RedisError, OSError) as exc:
            _LOGGER.warning("Directory write to %s failed: %s", key, exc)

    async def _get(self, key: str) -> Optional[str]:
        try:
            return await self._connection.client.get(key)
        except (RedisError, OSError) as exc:
            _LOGGER.warning("Directory read of %s failed: %s", key, exc)
            return None

    async def _delete(self, key: str) -> None:
        try:
            await self._connection.client.delete(key)
        except (RedisError, OSError) as exc:
            _LOGGER.warning("Directory delete of %s failed: %s", key, exc)
