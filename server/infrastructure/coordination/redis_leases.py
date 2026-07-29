"""Redis lease store — who owns which room, with a TTL and a fencing token.

Owns: the two keys a lease lives in and the scripts that keep acquire, renew and
release atomic.
Must not own: the renewal schedule (`RoomOwnership` runs it), placement
(`rendezvous_owner` decides it), or what owning a room permits.

**Two keys per room, and why the counter is separate.** `room:<id>:owner` holds
the owner and expires; `room:<id>:fence` holds the monotonically increasing
token and does not. If the token lived in the expiring key it would reset to 1
every time a room went briefly unowned, and two generations of owner would
compare equal — which is precisely the comparison the token exists to make
decidable.

**Every operation is a script**, because each one is a read followed by a
conditional write and the two must not be separable. A `GET` then `SET` renewal
is the textbook failure: between them the lease expires, another instance takes
it, and the `SET` hands it back to the instance that already lost it.
"""

import logging
import time
from typing import Optional

from server.domain.coordination.leases import DEFAULT_LEASE_TTL_SECONDS, Lease
from server.infrastructure.coordination.redis_client import RedisConnection, scripted

_LOGGER = logging.getLogger(__name__)

KEY_OWNER = "room:owner"
KEY_FENCE = "room:fence"

_MILLISECONDS = 1000

# `SET NX PX` in spirit, with the fencing token bumped in the same unit. An
# instance that already owns the room re-acquires it rather than being refused,
# so a retry after a slow response is not mistaken for a competitor.
_ACQUIRE_LUA = """
local owner_key, fence_key = KEYS[1], KEYS[2]
local owner, ttl_ms = ARGV[1], tonumber(ARGV[2])

local held = redis.call('GET', owner_key)
if held and held ~= owner then
  return nil
end

local token
if held == owner then
  -- Already ours: extend it and keep the token, so a retry does not look like
  -- a new generation to anything comparing tokens.
  token = tonumber(redis.call('GET', fence_key)) or 1
else
  token = redis.call('INCR', fence_key)
end
redis.call('SET', owner_key, owner, 'PX', ttl_ms)
return {owner, tostring(token)}
"""

# Extends only if this instance still holds the room *and* holds the generation
# it thinks it does. A renewal that checked only the owner would let an instance
# that lost and regained the room extend a claim it made two generations ago.
_RENEW_LUA = """
local owner_key, fence_key = KEYS[1], KEYS[2]
local owner, token, ttl_ms = ARGV[1], ARGV[2], tonumber(ARGV[3])

if redis.call('GET', owner_key) ~= owner then
  return 0
end
if tostring(redis.call('GET', fence_key)) ~= token then
  return 0
end
redis.call('PEXPIRE', owner_key, ttl_ms)
return 1
"""

# Releases only our own claim. Deleting unconditionally would let a finishing
# room evict the lease of whichever instance took it over after a failover.
_RELEASE_LUA = """
local owner_key, fence_key = KEYS[1], KEYS[2]
local owner, token = ARGV[1], ARGV[2]

if redis.call('GET', owner_key) == owner
   and tostring(redis.call('GET', fence_key)) == token then
  redis.call('DEL', owner_key)
end
return 1
"""


class RedisLeaseStore:
    """Room ownership in Redis: `SET NX PX 30000`, renewed, with fencing."""

    def __init__(
        self,
        connection: RedisConnection,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> None:
        self._connection = connection
        self._ttl_seconds = ttl_seconds
        self._acquire = connection.script(_ACQUIRE_LUA)
        self._renew = connection.script(_RENEW_LUA)
        self._release = connection.script(_RELEASE_LUA)

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    def _keys(self, room_id: str):
        return [
            self._connection.key(KEY_OWNER, room_id),
            self._connection.key(KEY_FENCE, room_id),
        ]

    async def acquire(self, room_id: str, owner: str) -> Optional[Lease]:
        result = await scripted(
            self._acquire, self._keys(room_id), [owner, int(self._ttl_seconds * _MILLISECONDS)]
        )
        if not result:
            return None
        _, token = result
        return Lease(
            room_id=room_id,
            owner=owner,
            fencing_token=int(token),
            expires_at=time.monotonic() + self._ttl_seconds,
        )

    async def renew(self, lease: Lease) -> Optional[Lease]:
        extended = await scripted(
            self._renew,
            self._keys(lease.room_id),
            [lease.owner, str(lease.fencing_token), int(self._ttl_seconds * _MILLISECONDS)],
        )
        if not extended:
            _LOGGER.warning(
                "Lost the lease on room %s (owner=%s, token=%d)",
                lease.room_id, lease.owner, lease.fencing_token,
            )
            return None
        return Lease(
            room_id=lease.room_id,
            owner=lease.owner,
            fencing_token=lease.fencing_token,
            expires_at=time.monotonic() + self._ttl_seconds,
        )

    async def release(self, lease: Lease) -> None:
        await scripted(
            self._release,
            self._keys(lease.room_id),
            [lease.owner, str(lease.fencing_token)],
        )

    async def owner_of(self, room_id: str) -> Optional[str]:
        try:
            return await self._connection.client.get(self._connection.key(KEY_OWNER, room_id))
        except Exception as exc:
            _LOGGER.warning("Reading the owner of room %s failed: %s", room_id, exc)
            return None
