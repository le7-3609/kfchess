"""Material scoring — derived state maintained purely by observing events.

Owns: each color's captured-material total, and announcing it as a
ScoreUpdatedEvent.
Must not own: board mutation, rules, rendering, or timing. It never reads the
board or the game state — capture events are its only input, which is what
lets it sit outside the simulation entirely.
"""

from typing import Dict

from kungfu_chess.events import (
    Event,
    EventBus,
    GameStartedEvent,
    Observer,
    PieceCapturedEvent,
    ScoreUpdatedEvent,
)
PIECE_VALUES: Dict[str, int] = {"P": 1, "N": 3, "B": 3, "R": 5, "Q": 9, "K": 0}


class MaterialScoreTracker(Observer):
    """Totals the material each color has captured, republishing it on change.

    Subscribes to capture events and publishes ScoreUpdatedEvent back onto the
    same bus, so the UI can display a score without knowing how it is derived
    and the engine never has to know a score exists at all.
    """

    def __init__(self, event_bus: EventBus) -> None:
        self._event_bus = event_bus
        self._scores: Dict[str, int] = {"w": 0, "b": 0}

    def on_event(self, event: Event) -> None:
        if isinstance(event, GameStartedEvent):
            self._reset()
        elif isinstance(event, PieceCapturedEvent):
            self._credit_capture(event)

    def score_for(self, color: str) -> int:
        return self._scores.get(color, 0)

    def _reset(self) -> None:
        """Clear both totals for a freshly installed board and announce the reset."""
        self._scores = {"w": 0, "b": 0}
        self._publish(at_ms=0)

    def _credit_capture(self, event: PieceCapturedEvent) -> None:
        """Award the captor the victim's value, ignoring friendly fire.

        Real-time play lets a piece take out its own side in a collision; that
        costs the owner material rather than earning them any, so it scores
        nothing for either color.
        """
        if event.captor_color == event.color:
            return
        self._scores[event.captor_color] = (
            self._scores.get(event.captor_color, 0) + PIECE_VALUES.get(event.piece_type, 0)
        )
        self._publish(at_ms=event.at_ms)

    def _publish(self, at_ms: int) -> None:
        self._event_bus.publish(
            ScoreUpdatedEvent(
                at_ms=at_ms,
                white_score=self._scores["w"],
                black_score=self._scores["b"],
            )
        )
