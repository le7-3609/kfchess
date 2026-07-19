"""Input command objects — the engine's typed command vocabulary (Layer 5).

Owns: the immutable DTOs GameEngine dispatches on (click, right-click, wait,
print board).
Must not own: DSL text parsing, pixel mapping, rule logic, or execution.

Declared in the engine layer — the innermost layer that consumes them — so the
outer layers that produce commands (io/ text parsing, input/ bots, runtime/
queues, ui/ windows) depend inward on this vocabulary, and engine never imports
them. Positions are grid Position(row, col) throughout; pixels exist only at
the UI's own boundary (input/board_mapper.py), which converts to Position
before anything reaches this vocabulary.

Commands are frozen so a queued command cannot be mutated between submission
and application — runtime/async_runner.py parks them in an asyncio queue that
is drained a tick later, on a different call stack than the producer.
"""

from dataclasses import dataclass

from kungfu_chess.model.position import Position


class GameCommand:
    """Marker base for every command ``GameEngine.execute_command`` accepts."""


@dataclass(frozen=True)
class ClickCommand(GameCommand):
    """Left-click at *pos*: select, move, or castle by selection state."""

    pos: Position


@dataclass(frozen=True)
class RightClickCommand(GameCommand):
    """Right-click at *pos*: jump the piece there in place."""

    pos: Position


@dataclass(frozen=True)
class WaitCommand(GameCommand):
    """Advance the simulation clock by *ms* and resolve pending motions."""

    ms: int


@dataclass(frozen=True)
class PrintBoardCommand(GameCommand):
    """Write the current board layout to the engine's configured printer."""
