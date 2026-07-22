"""Network broadcast observer — fans EventBus events out to seated players.

Layer: application (server/application)
Owns: mapping domain events onto wire frames and pushing them to every
recipient of a room.
Must not own: game rules, board mutation, session management, or transport —
it hands plain dicts to the seat contract, and the seat does the JSON encoding
and socket write.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, Optional, Set

from shared.events import (
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
from server.application.dtos.protocol_mapper import AlgebraicParser

_LOGGER = logging.getLogger(__name__)

# Maps a domain event type onto the wire frame it becomes. Declared once so
# adding an event means adding a row here, not another branch in a growing
# isinstance chain. Every builder takes the event and returns a plain dict.
_EVENT_SERIALIZERS: Dict[type, Callable[[Event], Dict[str, Any]]] = {
    GameStartedEvent: lambda event: {
        "type": "event_game_started",
        "rows": event.rows,
        "cols": event.cols,
        "at_ms": event.at_ms,
    },
    MoveStartedEvent: lambda event: {
        "type": "event_move_started",
        "color": event.color,
        "piece_type": event.piece_type,
        "from": AlgebraicParser.format_square(event.frm),
        "to": AlgebraicParser.format_square(event.to),
        "arrival_ms": event.arrival_ms,
        "at_ms": event.at_ms,
    },
    PieceMovedEvent: lambda event: {
        "type": "event_piece_moved",
        "color": event.color,
        "piece_type": event.piece_type,
        "from": AlgebraicParser.format_square(event.frm),
        "to": AlgebraicParser.format_square(event.to),
        "was_capture": event.was_capture,
        "at_ms": event.at_ms,
    },
    PieceCapturedEvent: lambda event: {
        "type": "event_piece_captured",
        "color": event.color,
        "piece_type": event.piece_type,
        "pos": AlgebraicParser.format_square(event.pos),
        "captor_color": event.captor_color,
        "captor_piece_type": event.captor_piece_type,
        "at_ms": event.at_ms,
    },
    MoveAbortedEvent: lambda event: {
        "type": "event_move_aborted",
        "color": event.color,
        "piece_type": event.piece_type,
        "from": AlgebraicParser.format_square(event.frm),
        "stopped_at": AlgebraicParser.format_square(event.stopped_at),
        "reason": event.reason,
        "at_ms": event.at_ms,
    },
    PiecePromotedEvent: lambda event: {
        "type": "event_piece_promoted",
        "color": event.color,
        "from_piece_type": event.from_piece_type,
        "to_piece_type": event.to_piece_type,
        "pos": AlgebraicParser.format_square(event.pos),
        "at_ms": event.at_ms,
    },
    ScoreUpdatedEvent: lambda event: {
        "type": "event_score_updated",
        "white_score": event.white_score,
        "black_score": event.black_score,
        "at_ms": event.at_ms,
    },
    GameEndedEvent: lambda event: {
        "type": "event_game_ended",
        "reason": event.reason,
        "winner": event.winner,
        "at_ms": event.at_ms,
    },
}


class NetworkBroadcastObserver(Observer):
    """Subscribes to EventBus and dispatches serialized event messages to network clients."""

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._loop = loop
        self._recipients: Set[Any] = set()

    def add_recipient(self, recipient: Any) -> None:
        self._recipients.add(recipient)

    def remove_recipient(self, recipient: Any) -> None:
        self._recipients.discard(recipient)

    def on_event(self, event: Event) -> None:
        """Synchronous Observer entry point called during simulation tick."""
        payload = self._serialize_event(event)
        if payload is None:
            return

        # Schedule async broadcast to all recipients
        try:
            loop = self._loop or asyncio.get_running_loop()
            loop.create_task(self._broadcast(payload))
        except RuntimeError:
            pass

    async def _broadcast(self, payload: Dict[str, Any]) -> None:
        for recipient in list(self._recipients):
            try:
                if hasattr(recipient, "send"):
                    await recipient.send(payload)
                elif hasattr(recipient, "send_message"):
                    await recipient.send_message(payload)
            except Exception as exc:
                _LOGGER.warning("Broadcast send failed for recipient %r: %s", recipient, exc)

    def _serialize_event(self, event: Event) -> Optional[Dict[str, Any]]:
        serializer = _EVENT_SERIALIZERS.get(type(event))
        return serializer(event) if serializer is not None else None
