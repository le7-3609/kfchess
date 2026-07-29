"""Ownership leases — which instance is allowed to compute which room.

Layer: domain (server/domain/coordination)
Owns: the lease value type, the fencing-token rule, the placement function, and
the contract a lease store must satisfy — plus the in-process implementation.
Must not own: the store (a Redis driver satisfies this), the renewal loop
(`RoomOwnership` in the application layer runs it), or the game.

**Ownership must be single and exclusive.** If two instances computed the same
room, nothing would make them agree — one could rule a move legal and the other
illegal, and the game would have two divergent histories with no way to
reconcile them. So every room has exactly one owner, only the owner may publish
state changes for it, and every other instance may forward requests to it but
never act on the room itself.

**Ownership is a lease, not an assignment.** A fixed range ("instance A owns
rooms 1-100,000") desynchronizes within seconds at 83,000 rooms created and
finished per second. A lease is taken with `SET NX PX 30000` — set only if
absent, expiring in 30 seconds — and renewed every 10. An owner that crashes
stops renewing, the key expires, and another instance acquires it.

**A crashed owner loses its in-flight games, and that is the accepted
behaviour** (Section 15 of Server_Design.md). Games last 30 to 90 seconds;
checkpointing every position, clock and cooldown to survive a crash costs more
complexity than a sub-minute session is worth. What the lease buys is not game
continuity but **blast radius**: a crash loses the rooms one instance owned, not
the fleet's.

**Fencing tokens guard against false failover.** A network hiccup, not a crash,
can also make an owner miss a renewal — and that owner does not know it. Each
acquisition carries a strictly increasing token, so a stale owner's claim is
recognisable as stale. In practice the enforcement is structural rather than
arithmetic: the owner is the sole subscriber to its room's command subject and
the sole publisher on its event subject, and both follow the lease, so a
resurrected old owner physically has nowhere to publish.
"""

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Dict, Optional, Protocol, Sequence, Tuple

_LOGGER = logging.getLogger(__name__)

# Long enough to survive a garbage-collection pause or a brief network stall,
# short enough that a crashed owner's rooms are re-placeable inside the length of
# one game. Renewal runs at a third of it, so two consecutive renewals must fail
# before ownership is genuinely at risk.
DEFAULT_LEASE_TTL_SECONDS = 30.0
DEFAULT_RENEWAL_INTERVAL_SECONDS = 10.0

_HASH_ENCODING = "utf-8"


@dataclass(frozen=True)
class Lease:
    """One instance's time-bounded claim on one room."""

    room_id: str
    owner: str
    # Strictly increasing per room across every acquisition, by anyone. Two
    # instances that both believe they own a room can always be ordered by it,
    # which is what makes "reject the older claim" a decidable rule rather than
    # a guess.
    fencing_token: int
    expires_at: float

    def is_held_by(self, instance_id: str) -> bool:
        return self.owner == instance_id

    def supersedes(self, other: Optional["Lease"]) -> bool:
        """Whether this claim is newer than *other*'s for the same room."""
        if other is None:
            return True
        if other.room_id != self.room_id:
            raise ValueError("Leases for different rooms are not comparable")
        return self.fencing_token > other.fencing_token


class LeaseStore(Protocol):
    """Where ownership claims are recorded."""

    async def acquire(self, room_id: str, owner: str) -> Optional[Lease]:
        """Claim *room_id* for *owner*, or None if someone else holds it."""

    async def renew(self, lease: Lease) -> Optional[Lease]:
        """Extend a lease this owner still holds; None if it was lost.

        Must verify the owner *and* the fencing token before extending. A bare
        `EXPIRE` would let an instance whose lease expired and was taken by
        someone else push the new owner's deadline out — resurrecting its own
        claim by refreshing a key it no longer owns.
        """

    async def release(self, lease: Lease) -> None:
        """Give up a lease, so a finished room is re-placeable immediately."""

    async def owner_of(self, room_id: str) -> Optional[str]:
        """Which instance currently holds *room_id*, or None."""


def rendezvous_owner(room_id: str, instances: Sequence[str]) -> Optional[str]:
    """Choose the instance a room should be placed on.

    Rendezvous (highest-random-weight) hashing rather than modulo: when the
    instance set changes, only the rooms that hashed highest for the departing
    instance move. Modulo would remap almost every room on every scale event,
    which at this fleet's churn rate is a continuous reshuffle.

    Deterministic across instances — every one of them computes the same answer
    from the same live set, so placement needs no coordinator and no agreement
    protocol, only a shared view of who is alive.
    """
    if not instances:
        return None
    return max(instances, key=lambda instance: _weight(room_id, instance))


def _weight(room_id: str, instance: str) -> Tuple[int, str]:
    """The room's affinity for one instance.

    The instance name is the tiebreaker so two instances that hash identically
    still order deterministically, rather than resolving by whichever the
    sequence happened to list first.
    """
    digest = hashlib.blake2b(
        f"{room_id}\x00{instance}".encode(_HASH_ENCODING), digest_size=8
    ).digest()
    return (int.from_bytes(digest, "big"), instance)


class InProcessLeases:
    """The no-infrastructure lease store, for a process that owns everything.

    Not a stub: a single-instance deployment genuinely has one owner, and this
    records it — including the fencing token, so the ordering rule is exercised
    by the default configuration rather than only by the Redis one.
    """

    def __init__(
        self, ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS, time_fn=time.monotonic
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._time_fn = time_fn
        self._leases: Dict[str, Lease] = {}
        self._tokens: Dict[str, int] = {}

    async def acquire(self, room_id: str, owner: str) -> Optional[Lease]:
        held = self._live_lease(room_id)
        if held is not None and not held.is_held_by(owner):
            return None

        # Re-acquiring a room this instance already holds keeps the generation.
        # Bumping it would invalidate the `Lease` object the owner's own renewal
        # loop is holding, so the next renewal would fail its token check and the
        # instance would surrender a room nobody took from it.
        token = held.fencing_token if held is not None else self._tokens.get(room_id, 0) + 1
        self._tokens[room_id] = token
        lease = Lease(
            room_id=room_id,
            owner=owner,
            fencing_token=token,
            expires_at=self._time_fn() + self._ttl_seconds,
        )
        self._leases[room_id] = lease
        return lease

    async def renew(self, lease: Lease) -> Optional[Lease]:
        held = self._live_lease(lease.room_id)
        if held is None or not held.is_held_by(lease.owner):
            return None
        if held.fencing_token != lease.fencing_token:
            # Someone else took and released it in between; this instance's
            # claim is a generation behind and must not be extended.
            return None

        renewed = Lease(
            room_id=lease.room_id,
            owner=lease.owner,
            fencing_token=lease.fencing_token,
            expires_at=self._time_fn() + self._ttl_seconds,
        )
        self._leases[lease.room_id] = renewed
        return renewed

    async def release(self, lease: Lease) -> None:
        held = self._leases.get(lease.room_id)
        if held is not None and held.fencing_token == lease.fencing_token:
            del self._leases[lease.room_id]

    async def owner_of(self, room_id: str) -> Optional[str]:
        lease = self._live_lease(room_id)
        return lease.owner if lease is not None else None

    def _live_lease(self, room_id: str) -> Optional[Lease]:
        """The lease on *room_id*, expiring it lazily if its deadline passed."""
        lease = self._leases.get(room_id)
        if lease is None:
            return None
        if self._time_fn() >= lease.expires_at:
            del self._leases[room_id]
            return None
        return lease
