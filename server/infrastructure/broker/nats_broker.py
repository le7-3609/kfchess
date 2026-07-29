"""NATS broker driver — core subjects for room events, JetStream for results.

Owns: the connection, the JetStream stream definition, JSON encoding, and the
mapping of the two delivery contracts onto the two NATS mechanisms.
Must not own: subject names (declared in the domain port), event serialization
(`_EVENT_SERIALIZERS` in `broadcast_observer.py` already does it), or retry
policy for a game's simulation.

**One connection, two mechanisms.** Core NAT S publish is fire-and-forget: the
message is delivered to whoever is subscribed at that instant and is gone. That
is exactly right for `room.*.events`, where a lost message costs at most one
reconciliation interval of drift and where adding an ack round trip to every
move would add latency to the one number a player can feel. JetStream persists
to a stream and redelivers until acknowledged, which is exactly right for
`game.finished`, where there is no later frame that repairs a game nobody wrote.

**Failures are contained on the fire-and-forget path and reported on the durable
one.** A room's tick must not be able to fail because the broker hiccuped, so
`publish` logs and returns. `publish_durable` returns False, because its caller
has to know: an unstored finished game needs to be retried or, at minimum,
counted.
"""

import json
import logging
from typing import Any, Dict, Optional

try:
    import nats
    from nats.js.api import RetentionPolicy, StreamConfig
    from nats.js.errors import BadRequestError
except ImportError:  # pragma: no cover - exercised only where nats-py is absent
    nats = None  # type: ignore[assignment]

from server.domain.coordination.broker import MessageHandler

_LOGGER = logging.getLogger(__name__)

DEFAULT_NATS_URL = "nats://localhost:4222"

# The durable stream holding finished games. Named separately from the subject
# because a stream may capture several subjects, and because the consumer group
# binds to the stream rather than to any one of them.
STREAM_GAME_RESULTS = "KFCHESS_GAME_RESULTS"
STREAM_SUBJECTS = ("game.>",)

# WorkQueue rather than Limits: a message is removed once a consumer
# acknowledges it, which is what makes a pool of persistence workers share the
# stream rather than each writing every game.
STREAM_RETENTION = "workqueue"

# How long a worker has to acknowledge before the message is redelivered. Sized
# well above a batched database write and well below a human noticing a missing
# game — and redelivery is safe because `ON CONFLICT (room_id) DO NOTHING`
# makes the write a no-op the second time.
DEFAULT_ACK_WAIT_SECONDS = 30

DEFAULT_ENCODING = "utf-8"


def nats_available() -> bool:
    """Whether the nats-py driver is importable in this process."""
    return nats is not None


class NatsBroker:
    """The `Broker` port over a NATS connection."""

    def __init__(
        self, url: str = DEFAULT_NATS_URL, ack_wait_seconds: int = DEFAULT_ACK_WAIT_SECONDS
    ) -> None:
        if nats is None:
            raise RuntimeError(
                "The 'nats-py' package is required for the NATS broker; install "
                "it or leave KFCHESS_NATS_URL unset to run in-process."
            )
        self._url = url
        self._ack_wait_seconds = ack_wait_seconds
        self._connection: Optional[Any] = None
        self._jetstream: Optional[Any] = None

    async def connect(self) -> None:
        """Open the connection and declare the durable stream.

        Declaring the stream here rather than in a deploy job is deliberate and
        different from the database schema: a stream declaration is idempotent
        and carries no data migration, so fifty replicas declaring the same one
        converge instead of racing. A table creation does neither, which is why
        that one moved to Alembic.
        """
        if self._connection is not None:
            return
        self._connection = await nats.connect(
            servers=[self._url],
            # Reconnect forever rather than giving up. A gateway that loses the
            # broker is not broken, it is temporarily unable to serve — /readyz
            # takes it out of rotation, and it comes back when NATS does.
            max_reconnect_attempts=-1,
        )
        self._jetstream = self._connection.jetstream()
        await self._ensure_stream()
        _LOGGER.info("Connected to NATS at %s", self._url)

    async def _ensure_stream(self) -> None:
        config = StreamConfig(
            name=STREAM_GAME_RESULTS,
            subjects=list(STREAM_SUBJECTS),
            retention=RetentionPolicy.WORK_QUEUE,
        )
        try:
            await self._jetstream.add_stream(config)
            _LOGGER.info("Declared JetStream stream %s", STREAM_GAME_RESULTS)
        except BadRequestError:
            # Already declared, by this replica's predecessor or by a peer that
            # started first. That is the converged state, not a failure.
            _LOGGER.debug("JetStream stream %s already exists", STREAM_GAME_RESULTS)

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.drain()
            self._connection = None
            self._jetstream = None

    async def is_connected(self) -> bool:
        return self._connection is not None and self._connection.is_connected

    async def publish(self, subject: str, payload: Dict[str, Any]) -> None:
        """Fire-and-forget publish; a failure is logged, never raised.

        This runs inside a room's tick. Raising here would let a broker blip end
        a game, and the reconciliation frame already repairs whatever a dropped
        event cost.
        """
        try:
            await self._require_connection().publish(subject, _encode(payload))
        except Exception as exc:
            _LOGGER.warning("Publish to %s failed: %s", subject, exc)

    async def subscribe(self, subject: str, handler: MessageHandler):
        """Subscribe to a core subject, decoding each message for *handler*.

        Flushes before returning. The client buffers the SUB and sends it with
        the next write, so without this the caller's very next publish can reach
        the server first and be dropped — a real race here, not a theoretical
        one: a gateway subscribes a player's frame subject and the `game_start`
        that seats them is published microseconds later.
        """
        connection = self._require_connection()
        subscription = await connection.subscribe(
            subject, cb=_decoding_callback(subject, handler)
        )
        await connection.flush()
        return _NatsSubscription(subscription)

    async def publish_durable(self, subject: str, payload: Dict[str, Any]) -> bool:
        """Publish to the stream, reporting whether JetStream stored it."""
        try:
            await self._require_jetstream().publish(subject, _encode(payload))
            return True
        except Exception as exc:
            _LOGGER.error("Durable publish to %s failed: %s", subject, exc)
            return False

    async def subscribe_durable(
        self, subject: str, handler: MessageHandler, queue_group: str
    ):
        """Join a durable consumer group, acknowledging only after a successful handle.

        The acknowledgement is the contract: a message is removed from the work
        queue when the handler returns, and redelivered if it raises or if the
        worker dies mid-write. Acknowledging first would turn every worker crash
        into a silently lost game.
        """
        subscription = await self._require_jetstream().subscribe(
            subject,
            # Both, and they must match. `durable` names the consumer that
            # survives a restart; `queue` is what makes it a *shared* one. With
            # `durable` alone JetStream creates a consumer bound to a single
            # subscription, and the second worker to start is refused outright —
            # which is a pool of one, discovered at the worst moment.
            durable=queue_group,
            queue=queue_group,
            manual_ack=True,
            cb=_acknowledging_callback(subject, handler),
        )
        await self._require_connection().flush()
        return _NatsSubscription(subscription)

    def _require_connection(self):
        if self._connection is None:
            raise RuntimeError("NATS connection is not open. Call connect() first.")
        return self._connection

    def _require_jetstream(self):
        if self._jetstream is None:
            raise RuntimeError("NATS connection is not open. Call connect() first.")
        return self._jetstream


class _NatsSubscription:
    """Wraps a NATS subscription so unsubscribing twice stays silent."""

    def __init__(self, subscription: Any) -> None:
        self._subscription: Optional[Any] = subscription

    async def unsubscribe(self) -> None:
        if self._subscription is None:
            return
        try:
            await self._subscription.unsubscribe()
        except Exception as exc:
            _LOGGER.debug("Unsubscribe raised: %s", exc)
        finally:
            self._subscription = None


def _decoding_callback(subject: str, handler: MessageHandler):
    async def on_message(message: Any) -> None:
        payload = _decode(subject, message.data)
        if payload is None:
            return
        try:
            await handler(payload)
        except Exception as exc:
            _LOGGER.warning("Handler for %s raised: %s", subject, exc)

    return on_message


def _acknowledging_callback(subject: str, handler: MessageHandler):
    async def on_message(message: Any) -> None:
        payload = _decode(subject, message.data)
        if payload is None:
            # Unparseable: acknowledge it rather than letting it redeliver
            # forever. A message no consumer can ever read is a poison pill, and
            # leaving it at the head of a work queue stalls every game behind it.
            await message.ack()
            return
        try:
            await handler(payload)
        except Exception as exc:
            _LOGGER.error("Durable handler for %s raised, leaving unacked: %s", subject, exc)
            return
        await message.ack()

    return on_message


def _encode(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload).encode(DEFAULT_ENCODING)


def _decode(subject: str, raw: bytes) -> Optional[Dict[str, Any]]:
    try:
        decoded = json.loads(raw.decode(DEFAULT_ENCODING))
    except (ValueError, UnicodeDecodeError) as exc:
        _LOGGER.error("Undecodable message on %s: %s", subject, exc)
        return None
    if not isinstance(decoded, dict):
        _LOGGER.error("Message on %s was not an object: %r", subject, decoded)
        return None
    return decoded
