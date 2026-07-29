"""Redis matchmaking store — the waiting list, shared by every replica.

Owns: the three keys the queue lives in, and the two Lua scripts that make a
pop atomic across the fleet.
Must not own: the ELO bound or the wait timeout — both arrive as arguments from
MatchmakingQueue, which is where that policy belongs.

Two ordered sets rather than one, because the queue is read two different ways
and each read wants its own ordering:

* `mm:elo`  — member=username, score=rating. `ZRANGEBYSCORE elo-100 elo+100`
  answers "who is compatible with this player" in O(log N + k), replacing the
  O(n^2) pair scan the in-process list needed.
* `mm:wait` — member=username, score=join time. Gives "who has waited longest"
  in O(1) for pairing, and "who has waited too long" as one range query for the
  timeout sweep.

A hash holds the rest of the ticket. The alternative — encoding it into the
sorted-set member — would make every member unique and destroy the ability to
address a player by username.
"""

import json
import logging
import time
from typing import Any, List, Optional, Tuple

from server.domain.matchmaking.queue_backend import QueueTicket
from server.infrastructure.coordination.redis_client import RedisConnection, scripted

_LOGGER = logging.getLogger(__name__)

KEY_ELO = "mm:elo"
KEY_WAIT = "mm:wait"
KEY_TICKET = "mm:ticket"

# How many of the longest-waiting players are tried as the anchor of a pair
# before giving up for this cycle. Bounds the script's worst case: without it, a
# queue full of mutually incompatible ratings would scan every member on every
# poll, twenty times a second, forever.
DEFAULT_PAIR_SCAN_LIMIT = 32

# Ceiling on one timeout sweep, for the same reason and because Lua's `unpack`
# has a stack limit that a truly unbounded eviction list would exceed.
DEFAULT_EXPIRY_SWEEP_LIMIT = 256

# Pops the longest-waiting player who has a compatible partner, together with
# that partner — or nothing at all. This is the atomicity the design calls a
# hard requirement: between the ZRANGEBYSCORE that finds the partner and the
# ZREMs that evict both, no other replica can run a command, because Redis
# executes a script as a single unit.
_POP_PAIR_LUA = """
local wait_key, elo_key, ticket_key = KEYS[1], KEYS[2], KEYS[3]
local max_diff = tonumber(ARGV[1])
local scan_limit = tonumber(ARGV[2])

local anchors = redis.call('ZRANGE', wait_key, 0, scan_limit - 1)
for i = 1, #anchors do
  local anchor = anchors[i]
  local anchor_elo = redis.call('ZSCORE', elo_key, anchor)
  if anchor_elo then
    local candidates = redis.call(
      'ZRANGEBYSCORE', elo_key, anchor_elo - max_diff, anchor_elo + max_diff
    )
    local partner, partner_wait = nil, nil
    for j = 1, #candidates do
      local candidate = candidates[j]
      if candidate ~= anchor then
        local waited = tonumber(redis.call('ZSCORE', wait_key, candidate))
        if waited and (partner_wait == nil or waited < partner_wait) then
          partner, partner_wait = candidate, waited
        end
      end
    end
    if partner then
      local first = redis.call('HGET', ticket_key, anchor)
      local second = redis.call('HGET', ticket_key, partner)
      redis.call('ZREM', wait_key, anchor, partner)
      redis.call('ZREM', elo_key, anchor, partner)
      redis.call('HDEL', ticket_key, anchor, partner)
      return {first, second}
    end
  end
end
return {}
"""

# Evicts everyone whose join time is at or before the cutoff, in one unit, and
# returns their tickets. Every replica sweeps the same store, so an eviction
# that was not atomic would hand the same timed-out player a bot on two replicas.
_POP_EXPIRED_LUA = """
local wait_key, elo_key, ticket_key = KEYS[1], KEYS[2], KEYS[3]
local cutoff = tonumber(ARGV[1])
local sweep_limit = tonumber(ARGV[2])

local expired = redis.call(
  'ZRANGEBYSCORE', wait_key, '-inf', cutoff, 'LIMIT', 0, sweep_limit
)
if #expired == 0 then
  return {}
end

local tickets = {}
for i = 1, #expired do
  tickets[i] = redis.call('HGET', ticket_key, expired[i])
end
redis.call('ZREM', wait_key, unpack(expired))
redis.call('ZREM', elo_key, unpack(expired))
redis.call('HDEL', ticket_key, unpack(expired))
return tickets
"""

# Adds a ticket only if that username is not already waiting, so a client that
# sends `play` twice occupies one slot rather than two.
_ADD_LUA = """
local wait_key, elo_key, ticket_key = KEYS[1], KEYS[2], KEYS[3]
local username, elo, joined_at, ticket = ARGV[1], ARGV[2], ARGV[3], ARGV[4]

if redis.call('ZSCORE', wait_key, username) then
  return 0
end
redis.call('ZADD', wait_key, joined_at, username)
redis.call('ZADD', elo_key, elo, username)
redis.call('HSET', ticket_key, username, ticket)
return 1
"""

_REMOVE_LUA = """
local wait_key, elo_key, ticket_key = KEYS[1], KEYS[2], KEYS[3]
local username = ARGV[1]

if not redis.call('ZSCORE', wait_key, username) then
  return 0
end
redis.call('ZREM', wait_key, username)
redis.call('ZREM', elo_key, username)
redis.call('HDEL', ticket_key, username)
return 1
"""


class RedisQueueBackend:
    """MatchmakingQueue's store, kept in Redis so every replica sees one queue."""

    def __init__(
        self,
        connection: RedisConnection,
        pair_scan_limit: int = DEFAULT_PAIR_SCAN_LIMIT,
        expiry_sweep_limit: int = DEFAULT_EXPIRY_SWEEP_LIMIT,
    ) -> None:
        self._connection = connection
        self._pair_scan_limit = pair_scan_limit
        self._expiry_sweep_limit = expiry_sweep_limit
        self._keys = [
            connection.key(KEY_WAIT),
            connection.key(KEY_ELO),
            connection.key(KEY_TICKET),
        ]
        self._add = connection.script(_ADD_LUA)
        self._remove = connection.script(_REMOVE_LUA)
        self._pop_pair = connection.script(_POP_PAIR_LUA)
        self._pop_expired = connection.script(_POP_EXPIRED_LUA)
        self._cached_size = 0

    def now(self) -> float:
        """Wall-clock seconds, deliberately not `time.monotonic`.

        A monotonic clock counts from an arbitrary per-process origin, so a
        join time written by one replica is meaningless to the replica that
        reads it. Wall clock is the only reading two hosts can compare, and the
        queue timeout it feeds is measured in tens of seconds — far coarser than
        the drift NTP leaves between them.
        """
        return time.time()

    def cached_size(self) -> int:
        return self._cached_size

    async def add(self, ticket: QueueTicket) -> bool:
        added = await scripted(
            self._add,
            self._keys,
            [ticket.username, ticket.elo, ticket.joined_at, _encode(ticket)],
        )
        await self._refresh_size()
        return bool(added)

    async def remove(self, username: str) -> bool:
        removed = await scripted(self._remove, self._keys, [username])
        await self._refresh_size()
        return bool(removed)

    async def pop_pair(self, max_elo_diff: int) -> Optional[Tuple[QueueTicket, QueueTicket]]:
        raw = await scripted(
            self._pop_pair, self._keys, [max_elo_diff, self._pair_scan_limit]
        )
        # Refreshed whether or not a pair formed. The queue is shared now, so a
        # replica that pops nothing for a minute would otherwise keep reporting
        # the depth it last saw while other replicas filled and drained it — and
        # the gauge that feeds the autoscaler would be reading a stale number
        # from every idle replica.
        await self._refresh_size()
        if not raw:
            return None

        tickets = _decode_all(raw)
        if len(tickets) != 2:
            # Both members were evicted by the script regardless, so a ticket
            # that failed to decode is a corrupt entry that has now been cleaned
            # out rather than one still occupying a slot.
            _LOGGER.error("Popped pair did not decode to two tickets: %r", raw)
            return None
        return (tickets[0], tickets[1])

    async def pop_expired(self, timeout_seconds: float) -> List[QueueTicket]:
        cutoff = self.now() - timeout_seconds
        raw = await scripted(
            self._pop_expired, self._keys, [cutoff, self._expiry_sweep_limit]
        )
        await self._refresh_size()
        return _decode_all(raw) if raw else []

    async def _refresh_size(self) -> None:
        """Keep the synchronous gauge's number close to the truth.

        Updated after each mutation rather than read on scrape, because
        `queue_length` is called from a synchronous metrics gauge that must
        never block on a network round trip.
        """
        try:
            self._cached_size = int(await self._connection.client.zcard(self._keys[0]))
        except Exception as exc:
            _LOGGER.debug("Could not refresh cached queue size: %s", exc)


def _encode(ticket: QueueTicket) -> str:
    return json.dumps(
        {
            "username": ticket.username,
            "elo": ticket.elo,
            "joined_at": ticket.joined_at,
            "replica": ticket.replica,
            "user_id": ticket.user_id,
        }
    )


def _decode_all(raw: Any) -> List[QueueTicket]:
    tickets = []
    for entry in raw:
        ticket = _decode(entry)
        if ticket is not None:
            tickets.append(ticket)
    return tickets


def _decode(entry: Any) -> Optional[QueueTicket]:
    if not entry:
        return None
    try:
        fields = json.loads(entry)
        return QueueTicket(
            username=fields["username"],
            elo=int(fields["elo"]),
            joined_at=float(fields["joined_at"]),
            replica=fields.get("replica", ""),
            user_id=int(fields.get("user_id", 0)),
        )
    except (ValueError, KeyError, TypeError) as exc:
        _LOGGER.error("Discarding unreadable queue ticket %r: %s", entry, exc)
        return None
