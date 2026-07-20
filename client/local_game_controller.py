"""LocalGameController — plays a match against an in-process GameService (Layer 6 / client).

Owns: the offline match's clock (advanced from wall time on every poll),
the subscription that turns domain events into the listener callbacks
IGameController promises, and the wording of a locally-decided game result.
Must not own: sockets of any kind, tkinter widgets, or game rules — the
simulation is GameService's, reached only through that facade.

This is the "offline test" the architecture is meant to pass: nothing here
imports server, websockets, or asyncio, so a full game runs with no server
process and no socket open.
"""

import logging
import time
from typing import Callable, Dict, List, Optional, Type

from shared.bootstrap import build_realtime_service
from shared.bot_factory import build_bot_service
from shared.config import consts
from shared.events import (
    Event,
    GameEndedEvent,
    GameStartedEvent,
    Observer,
    PieceCapturedEvent,
    PieceMovedEvent,
    ScoreUpdatedEvent,
)
from shared.io.moves_log import MoveLogEntry
from shared.model.position import Position
from shared.service import GameService
from client.algebraic_notation import format_square
from client.game_controller import (
    GameControllerListener,
    GameNotice,
    GameSessionInfo,
    IGameController,
    MatchHistoryPort,
    NoticeLevel,
)
from client.ui import consts as ui_consts

_LOGGER = logging.getLogger(__name__)

# An offline match simulates itself, so it wants the full frame budget rather
# than the laxer beat a networked window drains an inbox on.
_LOCAL_POLL_MS = ui_consts.TICK_MS

_HOTSEAT_OPPONENT_NAME = "Local opponent"
_BOT_OPPONENT_NAME = "Computer"


class _EventRelay(Observer):
    """Forwards the bus events this controller cares about and nothing else.

    Kept separate from the controller so `on_event` — which the EventBus calls
    mid-tick — is not part of IGameController's surface. It does no work of
    its own beyond handing the event on, because it runs part-way through a
    simulation step that must not be stalled.
    """

    def __init__(self, sink: Callable[[Event], None]) -> None:
        self._sink = sink

    def on_event(self, event: Event) -> None:
        self._sink(event)


class LocalGameController(IGameController):
    """Runs a Kung Fu Chess match entirely in memory, with no network at all.

    Serves both offline modes: two players sharing one machine (no seat is
    "yours", so the board stays unflipped and either color may be moved), and
    a single player against the built-in random bot.

    Domain events arrive synchronously, from inside the GameService call that
    caused them — which for `poll()` means part-way through advancing the
    clock. They are buffered rather than forwarded on arrival so the listener
    hears about them from `poll()` like every other callback, and always
    before the snapshot that already reflects them.
    """

    def __init__(
        self,
        service: GameService,
        assigned_color: Optional[str] = None,
        opponent_name: str = _HOTSEAT_OPPONENT_NAME,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._service = service
        self._assigned_color = assigned_color
        self._opponent_name = opponent_name
        self._clock = clock

        self._listener: Optional[GameControllerListener] = None
        self._relay = _EventRelay(self._buffer_event)
        self._pending_events: List[Event] = []
        self._last_tick: Optional[float] = None
        self._ended = False

        self._handlers: Dict[Type[Event], Callable[[Event], None]] = {
            PieceMovedEvent: self._emit_move,
            PieceCapturedEvent: self._emit_capture,
            ScoreUpdatedEvent: self._emit_score,
            GameStartedEvent: self._emit_game_started,
            GameEndedEvent: self._emit_game_ended,
        }

    @property
    def poll_interval_ms(self) -> int:
        return _LOCAL_POLL_MS

    @property
    def assigned_color(self) -> Optional[str]:
        return self._assigned_color

    @property
    def supports_jump(self) -> bool:
        return True

    @property
    def supports_preferences(self) -> bool:
        return True

    @property
    def history(self) -> Optional[MatchHistoryPort]:
        return self._service

    def start(self, listener: GameControllerListener) -> None:
        """Install the starting position and begin the match.

        Subscription happens before the board is installed so the listener
        also hears the GameStartedEvent that installing it publishes.
        """
        self._listener = listener
        self._service.subscribe(self._relay, *self._handlers)

        result = self._service.init_game(consts.STARTING_POSITION.splitlines())
        if not result.is_ok:
            _LOGGER.error("Offline game failed to start: %s", result.error)
            listener.on_notice(
                GameNotice(NoticeLevel.TERMINAL, f"Could not start the game: {result.error}")
            )
            return

        listener.on_session_started(
            GameSessionInfo(
                assigned_color=self._assigned_color,
                opponent_name=self._opponent_name,
            )
        )
        self._last_tick = self._clock()
        self.poll()

    def poll(self) -> None:
        """Advance the simulation by the wall time since the last poll, then report.

        Buffered events are flushed before the snapshot so the listener has
        already recorded a capture by the time it is handed the frame that
        capture belongs in.
        """
        self._advance_clock_to_now()
        self._flush_events()
        self._publish_snapshot()

    def submit_select(self, pos: Position) -> None:
        """Select *pos* in the engine, so the next snapshot carries its legal moves."""
        self._service.click(pos.row, pos.col)

    def submit_move(self, source: Position, target: Position) -> None:
        self._service.request_move(source, target)

    def submit_jump(self, pos: Position) -> None:
        self._service.right_click(pos.row, pos.col)

    def apply_preferences(self, ms_per_square: int, cooldown_ms: int) -> None:
        self._service.update_preferences(ms_per_square, cooldown_ms)

    def leave(self) -> None:
        """Detach from the event bus; there is no connection to close.

        Safe to call twice — the window's close handler and an explicit quit
        can both reach here, and unsubscribing a stranger is already a no-op.
        """
        self._service.unsubscribe(self._relay)
        self._listener = None

    def _advance_clock_to_now(self) -> None:
        """Feed the simulation the wall time that has elapsed since the last poll.

        A finished game stops advancing: its clock is what capture flashes and
        cooldown rings expire against, and letting it run on would age the
        final frame out from under the player reading it.
        """
        now = self._clock()
        if self._last_tick is None:
            self._last_tick = now
            return

        elapsed_ms = int((now - self._last_tick) * consts.MS_PER_SECOND)
        self._last_tick = now
        if elapsed_ms > 0 and not self._ended:
            self._service.advance_clock(elapsed_ms)

    def _buffer_event(self, event: Event) -> None:
        self._pending_events.append(event)

    def _flush_events(self) -> None:
        events, self._pending_events = self._pending_events, []
        for event in events:
            handler = self._handlers.get(type(event))
            if handler is not None:
                handler(event)

    def _publish_snapshot(self) -> None:
        if self._listener is None:
            return
        snapshot = self._service.get_snapshot()
        if snapshot is not None:
            self._listener.on_snapshot(snapshot)

    def _emit_move(self, event: PieceMovedEvent) -> None:
        """Compose the move-list entry from the event, not from the service's log.

        MovesLog subscribes to the same event, so reading it back here would
        make the move list depend on which subscriber the bus reached first.
        """
        notation = (
            f"{event.piece_type}{format_square(event.frm)}"
            f"{consts.NOTATION_MOVE_SEPARATOR}{format_square(event.to)}"
        )
        self._listener.on_move_recorded(
            MoveLogEntry(color=event.color, notation=notation, time_ms=event.at_ms)
        )

    def _emit_capture(self, event: PieceCapturedEvent) -> None:
        self._listener.on_capture(event.pos, event.at_ms)

    def _emit_score(self, event: ScoreUpdatedEvent) -> None:
        self._listener.on_score_changed(event.white_score, event.black_score)

    def _emit_game_started(self, event: GameStartedEvent) -> None:
        self._ended = False
        self._listener.on_score_changed(consts.STARTING_SCORE, consts.STARTING_SCORE)
        self._listener.on_notice(GameNotice.cleared())

    def _emit_game_ended(self, event: GameEndedEvent) -> None:
        self._ended = True
        self._listener.on_notice(GameNotice(NoticeLevel.TERMINAL, self._describe_ending(event)))

    def _describe_ending(self, event: GameEndedEvent) -> str:
        """Phrase a local result by seat, since offline no seat is "yours".

        A bot match is the exception: there the human does hold a color, so
        the result is worth stating as a win or a loss.
        """
        label = ui_consts.GAME_OVER_LABELS.get(event.reason, ui_consts.GAME_OVER_DEFAULT_LABEL)
        if event.winner is None:
            return f"Game over — {label.title()}."

        winner_name = ui_consts.COLOR_DISPLAY_NAMES.get(event.winner, event.winner)
        if self._assigned_color is None:
            return f"Game over — {winner_name} wins ({label.title()})."
        outcome = "you win" if event.winner == self._assigned_color else "you lose"
        return f"Game over — {outcome} ({label.title()})."


def build_hotseat_controller(ms_per_square: int, cooldown_ms: int) -> LocalGameController:
    """A two-players-one-machine match: no seat is assigned, so the board stays unflipped."""
    service = build_realtime_service(ms_per_square=ms_per_square)
    service.update_preferences(ms_per_square, cooldown_ms)
    return LocalGameController(service=service)


def build_bot_controller(
    player_color: str, ms_per_square: int, cooldown_ms: int
) -> LocalGameController:
    """A single-player match: the human takes *player_color*, the bot the other seat."""
    bot_color = consts.COLOR_BLACK if player_color == consts.COLOR_WHITE else consts.COLOR_WHITE
    service = build_bot_service(bot_color=bot_color, ms_per_square=ms_per_square)
    service.update_preferences(ms_per_square, cooldown_ms)
    return LocalGameController(
        service=service,
        assigned_color=player_color,
        opponent_name=_BOT_OPPONENT_NAME,
    )
