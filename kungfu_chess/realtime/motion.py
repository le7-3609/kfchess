"""Motion model — re-exports the Motion type used by the real-time arbiter.

The canonical Motion / Cooldown / EnPassantTarget dataclasses live in
``kungfu_chess.model.game_state``; this module re-exports them so that the
realtime layer can import from a single nearby location without reaching
through the full model path.

Must not own: chess legality, clicks, rendering, or script parsing.
"""

from kungfu_chess.model.game_state import (  # noqa: F401
    Movement as Motion,
    Movement,
    Cooldown,
    EnPassantTarget,
)
