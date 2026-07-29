"""Network broadcast observer — fans EventBus events out to a room's audience.

Layer: application (server/application)
Owns: mapping domain events onto wire frames and getting them to every recipient
of a room, whether that recipient's socket is held by this process or by another.
Must not own: game rules, board mutation, session management, or transport — it
hands plain dicts to the seat contract or to the broker, and neither the seat nor
the broker knows what the frame means.

**Where the frames go.** With no broker, straight to the sockets this process
holds — the original behaviour, and what a single-process deployment runs. With
one, to `room.<room_id>.events`, and whichever gateway holds a subscriber's
socket forwards it down. The publisher does not know where any subscriber runs,
which is the whole point: direct addressing between replicas would need every
instance to know how to reach every other in a fleet that is constantly being
rescheduled.

Both at once is normal rather than exceptional. The owner of a room very often
also holds one player's socket, and delivering locally as well as publishing
costs one dict copy and saves that player a broker round trip.

`_EVENT_SERIALIZERS`, the event types, and `core/events.py` are untouched by any
of this. The table below is the same table it was before the broker existed.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional, Set

from core.events import (
    Event,
    GameEndedEvent,
    GameStartedEvent,
    MoveAbortedEvent,
    MoveStartedEvent,
    Observer,
    PieceCapturedEvent,
    PieceMovedEvent,
    PiecePromotedEvent,
    ScoreUpdatedEvent,
)
from server.application.dtos import frame_fields as ff
from server.application.dtos import network_frames as nf
from server.application.dtos.protocol_mapper import AlgebraicParser

_LOGGER = logging.getLogger(__name__)

# Duck-typed send hooks a recipient may expose, probed in this order.
_ATTR_SEND = "send"
_ATTR_SEND_MESSAGE = "send_message"

# Maps a domain event type onto the wire frame it becomes. Declared once so
# adding an event means adding a row here, not another branch in a growing
# isinstance chain. Every builder takes the event and returns a plain dict.
_EVENT_SERIALIZERS: Dict[type, Callable[[Event], Dict[str, Any]]] = {
    GameStartedEvent: lambda event: {
        ff.FIELD_TYPE: nf.MSG_EVENT_GAME_STARTED,
        ff.FIELD_ROWS: event.rows,
        ff.FIELD_COLS: event.cols,
        ff.FIELD_AT_MS: event.at_ms,
    },
    MoveStartedEvent: lambda event: {
        ff.FIELD_TYPE: nf.MSG_EVENT_MOVE_STARTED,
        ff.FIELD_COLOR: event.color,
        ff.FIELD_PIECE_TYPE: event.piece_type,
        ff.FIELD_FROM: AlgebraicParser.format_square(event.frm),
        ff.FIELD_TO: AlgebraicParser.format_square(event.to),
        ff.FIELD_ARRIVAL_MS: event.arrival_ms,
        ff.FIELD_AT_MS: event.at_ms,
    },
    PieceMovedEvent: lambda event: {
        ff.FIELD_TYPE: nf.MSG_EVENT_PIECE_MOVED,
        ff.FIELD_COLOR: event.color,
        ff.FIELD_PIECE_TYPE: event.piece_type,
        ff.FIELD_FROM: AlgebraicParser.format_square(event.frm),
        ff.FIELD_TO: AlgebraicParser.format_square(event.to),
        ff.FIELD_WAS_CAPTURE: event.was_capture,
        ff.FIELD_AT_MS: event.at_ms,
    },
    PieceCapturedEvent: lambda event: {
        ff.FIELD_TYPE: nf.MSG_EVENT_PIECE_CAPTURED,
        ff.FIELD_COLOR: event.color,
        ff.FIELD_PIECE_TYPE: event.piece_type,
        ff.FIELD_POS: AlgebraicParser.format_square(event.pos),
        ff.FIELD_CAPTOR_COLOR: event.captor_color,
        ff.FIELD_CAPTOR_PIECE_TYPE: event.captor_piece_type,
        ff.FIELD_CAPTOR_FROM: AlgebraicParser.format_square(event.captor_frm),
        ff.FIELD_CAPTOR_TO: AlgebraicParser.format_square(event.captor_to),
        ff.FIELD_AT_MS: event.at_ms,
    },
    MoveAbortedEvent: lambda event: {
        ff.FIELD_TYPE: nf.MSG_EVENT_MOVE_ABORTED,
        ff.FIELD_COLOR: event.color,
        ff.FIELD_PIECE_TYPE: event.piece_type,
        ff.FIELD_FROM: AlgebraicParser.format_square(event.frm),
        ff.FIELD_STOPPED_AT: AlgebraicParser.format_square(event.stopped_at),
        ff.FIELD_REASON: event.reason,
        ff.FIELD_AT_MS: event.at_ms,
    },
    PiecePromotedEvent: lambda event: {
        ff.FIELD_TYPE: nf.MSG_EVENT_PIECE_PROMOTED,
        ff.FIELD_COLOR: event.color,
        ff.FIELD_FROM_PIECE_TYPE: event.from_piece_type,
        ff.FIELD_TO_PIECE_TYPE: event.to_piece_type,
        ff.FIELD_POS: AlgebraicParser.format_square(event.pos),
        ff.FIELD_AT_MS: event.at_ms,
    },
    ScoreUpdatedEvent: lambda event: {
        ff.FIELD_TYPE: nf.MSG_EVENT_SCORE_UPDATED,
        ff.FIELD_WHITE_SCORE: event.white_score,
        ff.FIELD_BLACK_SCORE: event.black_score,
        ff.FIELD_AT_MS: event.at_ms,
    },
    GameEndedEvent: lambda event: {
        ff.FIELD_TYPE: nf.MSG_EVENT_GAME_ENDED,
        ff.FIELD_REASON: event.reason,
        ff.FIELD_WINNER: event.winner,
        ff.FIELD_AT_MS: event.at_ms,
    },
}


class NetworkBroadcastObserver(Observer):
    """Subscribes to EventBus and dispatches serialized event frames to a room's audience."""

    def __init__(
        self,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        broker: Optional[Any] = None,
        room_subject: Optional[str] = None,
    ) -> None:
        self._loop = loop
        self._recipients: Set[Any] = set()
        # Both optional and both required together: a broker with no subject has
        # nowhere to publish, and a subject with no broker has nothing to publish
        # through. Neither is a usable half-configuration, so publishing is
        # enabled only when both arrived.
        self._broker = broker if room_subject else None
        self._room_subject = room_subject if broker else None

    @property
    def publishes_to_broker(self) -> bool:
        return self._broker is not None

    def add_recipient(self, recipient: Any) -> None:
        self._recipients.add(recipient)

    def remove_recipient(self, recipient: Any) -> None:
        self._recipients.discard(recipient)

    def on_event(self, event: Event) -> None:
        """Synchronous Observer entry point called during simulation tick.

        The dispatch is deferred onto a task because this runs part-way through
        a tick: awaiting a socket write or a broker publish inline would put
        network latency inside the simulation's 50 ms budget.
        """
        payload = self._serialize_event(event)
        if payload is None:
            return

        try:
            loop = self._loop or asyncio.get_running_loop()
            loop.create_task(self._broadcast(payload))
        except RuntimeError:
            pass

    async def _broadcast(self, payload: Dict[str, Any]) -> None:
        """Deliver one frame to every recipient, local and remote."""
        await self._publish_to_broker(payload)
        for recipient in list(self._recipients):
            await self._send_locally(recipient, payload)

    async def _publish_to_broker(self, payload: Dict[str, Any]) -> None:
        if self._broker is None:
            return
        try:
            await self._broker.publish(self._room_subject, payload)
        except Exception as exc:
            # Contained rather than raised: this is the fire-and-forget channel,
            # and the room's next reconciliation frame repairs whatever a
            # dropped event cost. A broker blip must not end a game.
            _LOGGER.warning("Publishing to %s failed: %s", self._room_subject, exc)

    @staticmethod
    async def _send_locally(recipient: Any, payload: Dict[str, Any]) -> None:
        try:
            if hasattr(recipient, _ATTR_SEND):
                await recipient.send(payload)
            elif hasattr(recipient, _ATTR_SEND_MESSAGE):
                await recipient.send_message(payload)
        except Exception as exc:
            _LOGGER.warning("Broadcast send failed for recipient %r: %s", recipient, exc)

    def _serialize_event(self, event: Event) -> Optional[Dict[str, Any]]:
        serializer = _EVENT_SERIALIZERS.get(type(event))
        return serializer(event) if serializer is not None else None
