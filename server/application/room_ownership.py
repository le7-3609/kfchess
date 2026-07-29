"""Room ownership — takes leases, renews them, and gives them up.

Layer: application (server/application)
Owns: the renewal loop, the reaction to losing a lease, and the drain that stops
this instance taking new rooms while letting its live ones finish.
Must not own: where leases are stored (a `LeaseStore` does), placement policy
(`rendezvous_owner` decides), or what a room does.

**The renewal loop is the liveness signal.** An instance holding a room renews
every 10 seconds against a 30-second TTL, so two consecutive failures — a
garbage-collection pause, a brief network stall — are survivable and a genuine
crash releases the room within 30 seconds. There is no heartbeat separate from
this: the lease *is* the heartbeat, and an instance that cannot renew is by
definition one that cannot be reached to be told anything either.

**Losing a lease is handled, not merely logged.** An instance that keeps
computing a room whose lease expired is the second authority the whole design
exists to prevent. So a failed renewal releases the room locally — which stops
its command subscription and its event publishing — rather than hoping the old
owner notices in time. The players are dropped, and reconnect into a fresh game;
that is the recorded decision (Section 15), and the lease's job is to bound how
many of them there are, not to save the game.

**Draining is not stopping.** On SIGTERM an instance stops accepting new rooms
and reports not-ready, but keeps renewing the leases it already holds and keeps
its games running to completion. Games last 30 to 90 seconds, so a termination
grace period of two to three minutes empties an instance without dropping one.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from server.domain.coordination.leases import (
    DEFAULT_LEASE_TTL_SECONDS,
    DEFAULT_RENEWAL_INTERVAL_SECONDS,
    Lease,
    rendezvous_owner,
)

_LOGGER = logging.getLogger(__name__)

# Called with the room id whose lease was lost, so the owner can shut it down.
LeaseLostCallback = Callable[[str], Awaitable[None]]


class RoomOwnership:
    """This instance's claims on the rooms it computes."""

    def __init__(
        self,
        store: Any,
        instance_id: str,
        on_lease_lost: Optional[LeaseLostCallback] = None,
        renewal_interval_seconds: float = DEFAULT_RENEWAL_INTERVAL_SECONDS,
        lease_ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> None:
        if renewal_interval_seconds >= lease_ttl_seconds:
            raise ValueError(
                "The renewal interval must be shorter than the lease TTL, or a "
                "lease expires before it can be renewed"
            )
        self._store = store
        self._instance_id = instance_id
        self._on_lease_lost = on_lease_lost
        self._renewal_interval_seconds = renewal_interval_seconds
        self._leases: Dict[str, Lease] = {}
        self._renewal_task: Optional[asyncio.Task] = None
        self._draining = False

    @property
    def instance_id(self) -> str:
        return self._instance_id

    @property
    def draining(self) -> bool:
        return self._draining

    @property
    def owned_room_count(self) -> int:
        """How many rooms this instance is responsible for, for the HPA gauge."""
        return len(self._leases)

    def owned_rooms(self) -> List[str]:
        return list(self._leases)

    def lease_for(self, room_id: str) -> Optional[Lease]:
        return self._leases.get(room_id)

    def owns(self, room_id: str) -> bool:
        return room_id in self._leases

    async def start(self) -> None:
        if self._renewal_task is None:
            self._renewal_task = asyncio.ensure_future(self._renewal_loop())

    async def stop(self) -> None:
        """Stop renewing and give up every lease.

        Releasing explicitly rather than letting the leases expire is what makes
        a clean shutdown fast: the rooms are re-placeable immediately instead of
        after a 30-second TTL during which nobody may take them.
        """
        if self._renewal_task is not None:
            self._renewal_task.cancel()
            try:
                await self._renewal_task
            except asyncio.CancelledError:
                pass
            self._renewal_task = None

        for lease in list(self._leases.values()):
            await self._store.release(lease)
        self._leases.clear()

    async def acquire(self, room_id: str) -> Optional[Lease]:
        """Claim a room for this instance, or report that someone else has it.

        Refused outright while draining: an instance on its way out must not
        take work it may not be alive to finish.
        """
        if self._draining:
            _LOGGER.debug("Refusing room %s: this instance is draining", room_id)
            return None

        lease = await self._store.acquire(room_id, self._instance_id)
        if lease is None:
            return None
        self._leases[room_id] = lease
        _LOGGER.info(
            "Acquired room %s (token=%d)", room_id, lease.fencing_token
        )
        return lease

    async def release(self, room_id: str) -> None:
        """Give up a room this instance has finished with."""
        lease = self._leases.pop(room_id, None)
        if lease is not None:
            await self._store.release(lease)
            _LOGGER.info("Released room %s", room_id)

    def begin_draining(self) -> None:
        """Stop taking new rooms; keep the ones already held running.

        Paired with `ReadinessProbe.begin_draining`, which is what removes this
        instance from the load balancer at the same moment.
        """
        self._draining = True
        _LOGGER.info(
            "Draining: no new rooms will be taken; %d still running", self.owned_room_count
        )

    def place(self, room_id: str, live_instances: List[str]) -> Optional[str]:
        """Which instance *should* own this room, by rendezvous hashing.

        Advisory. The lease is the authority — an instance that computes itself
        as the right owner still has to win the `SET NX`, and one that computes
        someone else may still legitimately hold a room it acquired before the
        instance set changed.
        """
        return rendezvous_owner(room_id, live_instances)

    async def _renewal_loop(self) -> None:
        """Renew every held lease on a fixed cadence, forever.

        One room's failure must not end renewal for the rest, so each is renewed
        independently and a loss is handled per room.
        """
        try:
            while True:
                await asyncio.sleep(self._renewal_interval_seconds)
                await self._renew_all()
        except asyncio.CancelledError:
            pass

    async def _renew_all(self) -> None:
        for room_id, lease in list(self._leases.items()):
            try:
                renewed = await self._store.renew(lease)
            except Exception as exc:
                # A renewal that raised is a renewal that did not happen, but it
                # is not proof the lease is gone — the TTL still has two-thirds
                # of its life left, and the next cycle will try again.
                _LOGGER.warning("Renewing room %s raised: %s", room_id, exc)
                continue

            if renewed is None:
                await self._surrender(room_id)
            else:
                self._leases[room_id] = renewed

    async def _surrender(self, room_id: str) -> None:
        """Give up a room whose lease this instance no longer holds.

        Reached when someone else has taken it — after a stall long enough for
        the TTL to lapse. Continuing to compute it would make two instances
        authoritative for one game, so the local copy goes away immediately.
        """
        self._leases.pop(room_id, None)
        _LOGGER.warning("Surrendering room %s: the lease is no longer ours", room_id)
        if self._on_lease_lost is None:
            return
        try:
            await self._on_lease_lost(room_id)
        except Exception as exc:
            _LOGGER.exception("Shutting down surrendered room %s failed: %s", room_id, exc)
