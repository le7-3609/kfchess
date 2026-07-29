"""Move idempotency — answering a repeated move frame without replaying it.

Layer: application (server/application)
Owns: the bounded per-room memory of recently-seen client move ids and the
Result each one produced.
Must not own: move legality, the room's state, or where a move id comes from
(the client generates it; presentation reads it off the frame).

Retries are not an edge case in this system. Mobile networks drop, and a deploy
of the WebSocket tier reconnects every client at once — so a client that
resubmits the move it is not sure landed is behaving correctly. Without this,
`AsyncGameRunner.submit_command` queues both copies into an unbounded queue and
the engine executes the move twice: in real-time chess that is a second,
unintended motion, not a harmless no-op.

Bounded rather than unbounded because the map is keyed by a client-supplied
value. Eviction is by insertion order, and the capacity is far larger than the
number of moves a game can contain, so a legitimate retry always finds its
entry while a flood of invented ids only evicts itself.
"""

import logging
from collections import OrderedDict
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

# A 30-to-90-second game produces tens of moves per player. A few hundred
# entries covers any real game with room to spare, at a few kilobytes per room.
DEFAULT_CAPACITY = 256


class MoveIdempotencyCache:
    """Remembers the outcome of each recently-seen move id."""

    def __init__(self, capacity: int = DEFAULT_CAPACITY) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._results: "OrderedDict[str, Any]" = OrderedDict()

    def __len__(self) -> int:
        return len(self._results)

    def recall(self, move_id: Optional[str]) -> Optional[Any]:
        """The Result this id already produced, or None if it is new.

        A frame with no id is always new: idempotency is opt-in, so a client
        that does not supply one keeps the old at-least-once behaviour rather
        than having unrelated moves collapse into a shared null key.
        """
        if not move_id:
            return None
        return self._results.get(move_id)

    def remember(self, move_id: Optional[str], result: Any) -> None:
        """Record *result* as the answer for *move_id*, evicting the oldest."""
        if not move_id:
            return
        self._results[move_id] = result
        self._results.move_to_end(move_id)
        while len(self._results) > self._capacity:
            evicted, _ = self._results.popitem(last=False)
            _LOGGER.debug("Evicted move id %r from the idempotency cache", evicted)
