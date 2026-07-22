"""Domain events and the pub/sub bus (Layer 1).

Owns: the Event value types the simulation announces, the Observer interface
subscribers implement, and the EventBus that routes one to the other.
Must not own: game rules, board mutation, rendering, timing, or any knowledge
of who subscribes.

This module is deliberately the innermost shared dependency: every layer may
import it, and it imports nothing but the Position value type and the constant
registry. That is what lets the UI observe the simulation without the
simulation ever naming the UI.

Events carry plain values (color/piece-type letters, Positions) rather than
live Piece or Board objects. A subscriber runs after the fact, so handing it a
mutable domain object would let it read state that has already moved on — or
reach back in and mutate the simulation from outside its own layer.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple, Type

from shared.config import consts
from shared.model.position import Position

_LOGGER = logging.getLogger(__name__)

# Re-exported from consts so subscribers can name a reason without reaching
# past the event module that hands it to them.
ABORT_REASON_FRIENDLY_COLLISION = consts.ABORT_REASON_FRIENDLY_COLLISION
ABORT_REASON_PATH_BLOCKED = consts.ABORT_REASON_PATH_BLOCKED
ABORT_REASON_CAPTURED_IN_FLIGHT = consts.ABORT_REASON_CAPTURED_IN_FLIGHT


@dataclass(frozen=True)
class Event:
    """Base class for everything published on the bus.

    *at_ms* is the simulation clock instant the event describes — not wall
    time. Subscribers that animate (a capture flash, say) must expire their
    effects against the same clock, since it only advances while the game runs.
    """

    at_ms: int


@dataclass(frozen=True)
class GameStartedEvent(Event):
    """A board was installed and play has (re)started."""

    rows: int
    cols: int


@dataclass(frozen=True)
class MoveStartedEvent(Event):
    """A piece left *frm* and is now in transit, due at *arrival_ms*.

    Published when the motion is registered, not when it lands — in real-time
    chess the travel itself is visible, so subscribers learn about a move at
    departure rather than on arrival.
    """

    color: str
    piece_type: str
    frm: Position
    to: Position
    arrival_ms: int


@dataclass(frozen=True)
class PieceMovedEvent(Event):
    """A piece completed its transit and is committed to *to*.

    *piece_type* is the type on arrival: a promoting pawn is swapped for its
    promoted piece before this is published, so it reads 'Q', not 'P'. The
    preceding PiecePromotedEvent carries both types.
    """

    color: str
    piece_type: str
    frm: Position
    to: Position
    was_capture: bool


@dataclass(frozen=True)
class PieceCapturedEvent(Event):
    """A piece was removed from play by *captor* at *pos*.

    Friendly fire is possible in real-time chess, so the captor's color is not
    necessarily the opponent's — subscribers that score material must compare
    *color* against *captor_color* rather than assume.

    *captor_frm*/*captor_to* are the capturing movement's own endpoints. They
    are the only reliable link back to the captor's move: a collision capture
    happens mid-transit at neither endpoint, and en passant strikes the
    bypassed pawn's square rather than the captor's destination, so *pos*
    alone cannot identify which move took the piece.
    """

    color: str
    piece_type: str
    pos: Position
    captor_color: str
    captor_piece_type: str
    captor_frm: Position
    captor_to: Position


@dataclass(frozen=True)
class MoveAbortedEvent(Event):
    """A move failed to complete and its piece stopped at *stopped_at*.

    *reason* is one of the ABORT_REASON_* constants in this module.
    """

    color: str
    piece_type: str
    frm: Position
    stopped_at: Position
    reason: str


@dataclass(frozen=True)
class PiecePromotedEvent(Event):
    """A pawn reached its promotion rank and was replaced by *to_piece_type*."""

    color: str
    from_piece_type: str
    to_piece_type: str
    pos: Position


@dataclass(frozen=True)
class ScoreUpdatedEvent(Event):
    """Captured-material totals changed. Scores are cumulative, not a delta."""

    white_score: int
    black_score: int


@dataclass(frozen=True)
class GameEndedEvent(Event):
    """The game reached a terminal position. *winner* is None for a draw."""

    reason: str
    winner: Optional[str]


class Observer(ABC):
    """Anything that reacts to events published on an EventBus."""

    @abstractmethod
    def on_event(self, event: Event) -> None:
        """React to *event*.

        Called synchronously from inside the publisher's call stack, which for
        most events is part-way through a simulation tick. Implementations
        should record what they need and return promptly rather than doing
        heavy work (or, in the UI's case, drawing) inline.
        """


@dataclass(frozen=True)
class _Subscription:
    """One observer's registration, optionally narrowed to certain event types."""

    observer: Observer
    event_types: Optional[Tuple[Type[Event], ...]]

    def wants(self, event: Event) -> bool:
        """True if this subscription should receive *event*.

        Matching is by isinstance, so subscribing to Event itself receives
        everything and a future event subclass reaches its parent's subscribers.
        """
        return self.event_types is None or isinstance(event, self.event_types)


class EventBus:
    """Subject side of the Observer pattern: holds subscriptions, dispatches events.

    Publishers hold a bus and know nothing about who listens; subscribers hold
    an Observer implementation and know nothing about who publishes. Neither
    side imports the other.
    """

    def __init__(self) -> None:
        self._subscriptions: List[_Subscription] = []

    def subscribe(self, observer: Observer, *event_types: Type[Event]) -> None:
        """Register *observer*, optionally only for the given *event_types*.

        With no event types the observer receives every event. Naming the types
        it actually handles keeps a subscriber from being woken for the high
        frequency events (every move, every tick) it would only discard.
        """
        self._subscriptions.append(
            _Subscription(observer=observer, event_types=event_types or None)
        )

    def unsubscribe(self, observer: Observer) -> None:
        """Drop every subscription belonging to *observer*. Unknown observers are ignored."""
        self._subscriptions = [
            sub for sub in self._subscriptions if sub.observer is not observer
        ]

    def publish(self, event: Event) -> None:
        """Deliver *event* to every matching subscriber, in subscription order.

        Dispatch is synchronous and depth-first: if a handler publishes a
        derived event, that inner dispatch runs to completion before this one
        resumes. A later subscriber can therefore see the derived event before
        the one that caused it — MaterialScoreTracker, subscribed first,
        emits ScoreUpdatedEvent while PieceCapturedEvent is still being
        delivered. Subscribers must treat each event on its own terms rather
        than relying on arrival order across event types.
        """
        # Iterated over a snapshot: a handler may subscribe, unsubscribe, or
        # publish while this loop is still running.
        for sub in tuple(self._subscriptions):
            if sub.wants(event):
                self._deliver(sub.observer, event)

    @staticmethod
    def _deliver(observer: Observer, event: Event) -> None:
        """Hand *event* to *observer*, containing any failure to that observer.

        A subscriber is a bystander to the simulation: publish() is called from
        the middle of a tick, with the board part-way through being resolved.
        Letting a UI or logging failure propagate would abandon that tick and
        leave the board in a state no rule produced, so a broken observer is
        logged and skipped instead of taking the game down with it.
        """
        try:
            observer.on_event(event)
        except Exception:
            _LOGGER.exception(
                "Observer %r failed handling %s", observer, type(event).__name__
            )
