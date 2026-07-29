"""Message broker — the port a room's events and results are published through.

Layer: domain (server/domain/coordination)
Owns: the subject vocabulary the fleet agrees on, the two delivery contracts
(fire-and-forget and durable), and the in-process implementation used when there
is no broker to talk to.
Must not own: NATS, Redis, or any wire format — a driver satisfies this from
`server/infrastructure/`, and the payloads are the plain dicts
`NetworkBroadcastObserver` already builds.

**Why a broker at all.** After Step 5 two replicas share a player population,
but a move computed on replica B still has to reach a socket held by replica A.
Direct addressing does not scale: every instance would need to know how to reach
every other, in a fleet that is constantly rescheduled. Instead every instance
talks only to the broker. The room's owner publishes to `room.<id>.events`, and
whichever gateway holds a subscriber's socket forwards it down. The publisher
does not know, and does not need to know, where any subscriber runs.

**Why two delivery contracts.** They are chosen from failure tolerance, not
familiarity:

* `room.<id>.events` is **fire-and-forget**. Lowest possible latency matters and
  occasional loss does not, because Step 2's reconciliation frame repairs any
  drift within seconds. Paying for durability here would buy nothing a
  five-second resync does not already provide.
* `game.finished` is **durable**. A lost message here is a game that is never
  saved and a rating that is never applied — there is no later frame that
  repairs it. It must survive a consumer crash and be redeliverable, which is
  exactly why the persistence writes it consumes are idempotent.
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Protocol

_LOGGER = logging.getLogger(__name__)

# One subject per room. Wildcarding on the room id is what lets a gateway
# subscribe to exactly the rooms whose sockets it holds, rather than filtering a
# fleet-wide firehose in Python. Carries only frames every recipient receives
# identically — the event stream.
SUBJECT_ROOM_EVENTS = "room.{room_id}.events"

# Inbound: a command from a player whose socket is on some other instance,
# addressed to whoever owns the room. Only the owner subscribes, which is what
# makes "one room, one authority" a property of the subscription rather than a
# rule someone has to remember.
SUBJECT_ROOM_COMMANDS = "room.{room_id}.commands"

# One subject per player, for the frames that are *not* identical per recipient:
# the per-recipient snapshot (one player's selection is theirs alone), the
# game-start and game-end frames, and errors. Keyed by player rather than by
# replica so a client that reconnects onto a different gateway keeps receiving.
SUBJECT_SESSION_FRAMES = "session.{username}.frames"

# One durable stream for every finished game, consumed by a pool of workers.
SUBJECT_GAME_FINISHED = "game.finished"

MessageHandler = Callable[[Dict[str, Any]], Awaitable[None]]


def room_events_subject(room_id: str) -> str:
    return SUBJECT_ROOM_EVENTS.format(room_id=room_id)


def room_commands_subject(room_id: str) -> str:
    return SUBJECT_ROOM_COMMANDS.format(room_id=room_id)


def session_frames_subject(username: str) -> str:
    return SUBJECT_SESSION_FRAMES.format(username=username)


class Broker(Protocol):
    """Publish/subscribe over the fleet, in two delivery contracts."""

    async def publish(self, subject: str, payload: Dict[str, Any]) -> None:
        """Send *payload* on *subject*, fire-and-forget.

        Returns as soon as the message is handed to the transport. A failure is
        contained, not raised: a room's tick must not be able to fail because a
        broker hiccuped, and the next reconciliation frame repairs what a
        dropped event cost.
        """

    async def subscribe(self, subject: str, handler: MessageHandler) -> "Subscription":
        """Deliver every message on *subject* to *handler* until unsubscribed."""

    async def publish_durable(self, subject: str, payload: Dict[str, Any]) -> bool:
        """Send *payload* on a persisted stream, reporting whether it was stored.

        The boolean matters: unlike `publish`, the caller of this one has to know
        it failed, because there is no later frame that repairs a finished game
        nobody recorded.
        """

    async def subscribe_durable(
        self, subject: str, handler: MessageHandler, queue_group: str
    ) -> "Subscription":
        """Consume a persisted stream as one of a named group of workers.

        The group is what makes a pool: each message goes to exactly one member,
        and a member that dies has its unacknowledged messages redelivered to
        another. Delivery is therefore at-least-once, which is why every write
        the handler performs must be idempotent.
        """

    async def is_connected(self) -> bool:
        """Whether this process can currently reach the broker."""


class Subscription(Protocol):
    """A live subscription, cancellable independently of the broker."""

    async def unsubscribe(self) -> None:
        """Stop delivering; safe to call twice."""


class InProcessBroker:
    """The no-infrastructure default: publish and subscribe within one process.

    Not a mock. It is what a single-process deployment genuinely runs on, and it
    is what keeps `pytest` green with no containers — so it has to honour the
    same contract, including containing a handler's exception rather than
    letting it escape into the publisher's tick.

    Durable publishes are held in a list and replayed to a group that subscribes
    later, which is the property real durability provides and the one consumers
    are written against: a worker that starts after a game finished must still
    see it.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, List[MessageHandler]] = {}
        self._durable_handlers: Dict[str, List[MessageHandler]] = {}
        self._pending_durable: Dict[str, List[Dict[str, Any]]] = {}

    async def publish(self, subject: str, payload: Dict[str, Any]) -> None:
        # A copy of the handler list, because a handler is allowed to
        # unsubscribe itself while being delivered to.
        for handler in list(self._handlers.get(subject, [])):
            await self._deliver(handler, subject, payload)

    async def subscribe(self, subject: str, handler: MessageHandler) -> "InProcessSubscription":
        self._handlers.setdefault(subject, []).append(handler)
        return InProcessSubscription(self._handlers, subject, handler)

    async def publish_durable(self, subject: str, payload: Dict[str, Any]) -> bool:
        handlers = self._durable_handlers.get(subject, [])
        if not handlers:
            self._pending_durable.setdefault(subject, []).append(payload)
            return True
        # One member of the group, not all of them — a work queue, not a
        # broadcast. Delivering to every worker would write the same game once
        # per worker.
        await self._deliver(handlers[0], subject, payload)
        return True

    async def subscribe_durable(
        self, subject: str, handler: MessageHandler, queue_group: str
    ) -> "InProcessSubscription":
        """Join the work queue for *subject*, draining whatever it already holds.

        *queue_group* is accepted and, in one process, has one possible value —
        the registry below is that group. It is part of the signature because
        the durable driver genuinely needs it, and a port whose in-process
        implementation quietly drops a parameter is a port that stops describing
        the contract.
        """
        self._durable_handlers.setdefault(subject, []).append(handler)
        for payload in self._pending_durable.pop(subject, []):
            await self._deliver(handler, subject, payload)
        return InProcessSubscription(self._durable_handlers, subject, handler)

    async def is_connected(self) -> bool:
        return True

    @staticmethod
    async def _deliver(handler: MessageHandler, subject: str, payload: Dict[str, Any]) -> None:
        """Hand one payload to one handler, containing any exception.

        A subscriber that raises must not abandon the publisher's work — the
        same reason `EventBus.publish` contains subscriber exceptions in `core`.
        """
        try:
            await handler(payload)
        except Exception as exc:
            _LOGGER.warning("Broker handler for %s raised: %s", subject, exc)


class InProcessSubscription:
    """Removes one handler from the registry it was added to."""

    def __init__(
        self, registry: Dict[str, List[MessageHandler]], subject: str, handler: MessageHandler
    ) -> None:
        self._registry = registry
        self._subject = subject
        self._handler = handler

    async def unsubscribe(self) -> None:
        handlers = self._registry.get(self._subject)
        if handlers and self._handler in handlers:
            handlers.remove(self._handler)
