"""Game room — network broadcast, background tasks, and persistence for a game.

Owns: network broadcast wiring, background task lifecycle (tick runner, bot
driver, disconnect countdown), and ELO persistence on forfeit or natural
game end.
Must not own: seat-assignment invariants, room lifecycle state, or move
authorization — those live in server.domain.room.game_room.GameRoom, the pure
aggregate this class composes.
"""

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from shared.bot_factory import build_random_bot
from shared.config import consts
from shared.events import Event, GameEndedEvent, Observer
from shared.model.game_state import Result
from shared.runtime.async_runner import AsyncGameRunner
from server.application.broadcast_observer import NetworkBroadcastObserver
from server.application.game_persistence_service import GamePersistenceService
from server.application.game_result import GameResult, PersistedMove, persisted_moves_from_log
from server.infrastructure.database.database import Database
from server.infrastructure.services.bot_driver import (
    DEFAULT_BOT_MOVE_INTERVAL_SECONDS,
    BotDriver,
)
from server.application.disconnect_handler import (
    DEFAULT_DISCONNECT_TIMEOUT_SECONDS,
    DisconnectHandler,
)
from server.domain.room.game_room import (
    ForfeitOutcome,
    GameEndOutcome,
    GameRoom as DomainGameRoom,
    RoomState,
)
from server.domain.room.room_role import RoomRole
from server.domain.player.player_interface import DEFAULT_BOT_USERNAME, BotPlayerAdapter
from server.application.dtos.response_frames import build_game_ended_message, build_game_state_message
from server.application.dtos.protocol_mapper import AlgebraicParser, SnapshotSerializer

_DISCONNECT_FORFEIT_REASON = "disconnection_timeout"

# Stored `result` for a game that ended because a player ran out the
# reconnection countdown, distinct from the wire-frame reason above.
_FORFEIT_RESULT = "timeout"


def _rating_delta(session: Any, new_elo: int) -> Dict[str, int]:
    """A `{new_elo, elo_change}` pair for the game-end frame.

    Must be read before *session*'s own `.elo` is overwritten with *new_elo*,
    since the change is computed against whatever `.elo` still holds.
    """
    return {"new_elo": new_elo, "elo_change": new_elo - session.elo}

_LOGGER = logging.getLogger(__name__)


# Invoked with the expiring room's id. Allowed to be a plain callable or a
# coroutine function, since the reaper on the other end has to await the room's
# own async teardown while a simpler listener (a metric, a log line) does not.
RoomExpiredCallback = Callable[[str], Optional[Awaitable[None]]]


class _RoomLifecycleObserver(Observer):
    """Reports the terminal event of a game back to the room that owns its bus.

    Deliberately does nothing but forward the event. It is called from inside
    EventBus dispatch, which runs part-way through a simulation tick, so any
    real work here would stall the tick it is riding on — the room turns this
    notification into deferred ELO settlement and teardown itself.
    """

    def __init__(self, on_game_ended: Callable[[GameEndedEvent], None]) -> None:
        self._on_game_ended = on_game_ended

    def on_event(self, event: Event) -> None:
        if isinstance(event, GameEndedEvent):
            self._on_game_ended(event)


class GameRoom:
    """Manages an active game instance's network, background tasks, and persistence."""

    def __init__(
        self,
        room_id: str,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        database: Optional[Database] = None,
        persistence_service: Optional[GamePersistenceService] = None,
        disconnect_timeout_seconds: int = DEFAULT_DISCONNECT_TIMEOUT_SECONDS,
        on_room_expired: Optional[RoomExpiredCallback] = None,
    ) -> None:
        self._domain = DomainGameRoom(room_id=room_id)
        self._loop = loop
        self._database = database
        self._persistence_service = persistence_service
        # Wall-clock instant the game became playable, stamped when the engine
        # is wired. Paired with game-end wall time to bound a saved game in real
        # time (the simulation clock only measures elapsed play, not calendar).
        self._started_at: Optional[datetime] = None

        self._core: Optional[Any] = None
        self._runner: Optional[AsyncGameRunner] = None
        self._broadcast_observer: Optional[NetworkBroadcastObserver] = None
        self._bot_driver: Optional[BotDriver] = None
        self._disconnect_handler = DisconnectHandler(
            game_room=self,
            timeout_seconds=disconnect_timeout_seconds,
            on_forfeit=self._apply_forfeit,
        )

        self._on_room_expired = on_room_expired
        self._expired = False
        # Held so the detached teardown task cannot be garbage-collected
        # mid-flight: the event loop keeps only weak references to tasks.
        self._expiry_task: Optional[asyncio.Task] = None
        # Same reasoning as _expiry_task: this races teardown as its own
        # detached task, so a reference must be kept alive until it completes.
        self._elo_settlement_task: Optional[asyncio.Task] = None
        self._lifecycle_observer = _RoomLifecycleObserver(on_game_ended=self._on_game_ended)

    @property
    def room_id(self) -> str:
        return self._domain.room_id

    @property
    def state(self) -> RoomState:
        return self._domain.state

    @property
    def is_full(self) -> bool:
        return self._domain.is_full

    @property
    def white_player(self) -> Optional[Any]:
        return self._domain.white_player

    @property
    def black_player(self) -> Optional[Any]:
        return self._domain.black_player

    @property
    def service(self):
        return self._domain.service

    @property
    def disconnect_handler(self) -> DisconnectHandler:
        return self._disconnect_handler

    @property
    def viewer_count(self) -> int:
        return self._domain.viewer_count

    @property
    def has_bot(self) -> bool:
        return self._bot_driver is not None

    def role_of(self, session: Any) -> Optional[RoomRole]:
        """Return the seat *session* holds here, or None if it is not a participant."""
        return self._domain.role_of(session)

    def opponent_of(self, session: Any) -> Optional[Any]:
        """Return the other seated player, or None if *session* holds no seat."""
        return self._domain.opponent_of(session)

    def find_player_by_username(self, username: str) -> Optional[Any]:
        """Look up a seated (White or Black) session by username, ignoring viewers."""
        return self._domain.find_player_by_username(username)

    def handle_disconnect(self, session: Any) -> bool:
        """Start the reconnection countdown for a seated player who dropped mid-game.

        Returns True if the countdown was started, meaning the seat must be
        preserved rather than freed. Returns False when there is no active
        game to preserve (viewer, or room not PLAYING) — the caller should
        fall back to immediately tearing down the participant.
        """
        if self._domain.state != RoomState.PLAYING:
            return False
        role = self._domain.role_of(session)
        if role not in (RoomRole.WHITE_PLAYER, RoomRole.BLACK_PLAYER):
            return False

        opponent = self._domain.opponent_of(session)
        self._disconnect_handler.handle_disconnect(session, opponent)
        return True

    async def handle_reconnect(self, username: str, new_websocket: Any) -> Optional[Any]:
        """Rebind a returning player's new WebSocket to their existing seat.

        Returns the restored session on success, or None if `username` does
        not match a currently-disconnected seat in this room.
        """
        session = self._domain.find_player_by_username(username)
        if session is None or not self._disconnect_handler.is_disconnected(session):
            return None

        await self._disconnect_handler.handle_reconnect(session, new_websocket)
        return session

    async def _apply_forfeit(self, disconnected_session: Any, opponent_session: Optional[Any]) -> None:
        """Disconnect-timeout callback: persist ELO, free the seat, end the room.

        Winner declaration and the game-state transition to "ended" already
        happen inside DisconnectHandler; this callback owns the side effects
        that are specifically GameRoom's responsibility.
        """
        # Computing the outcome touches .elo on both sessions, which a
        # database-less room (tests, ad-hoc GameRoom construction) has no
        # obligation to provide — so this stays gated on a database existing,
        # same as the persistence it feeds.
        outcome = (
            self._domain.compute_forfeit_outcome(disconnected_session, opponent_session)
            if self._database is not None
            else None
        )
        if outcome is not None:
            white_session, white_new_elo, black_session, black_new_elo = self._forfeit_outcome_by_seat(outcome)
            winner_color = opponent_session.color if opponent_session is not None else None
            await self._broadcast_game_ended(
                _DISCONNECT_FORFEIT_REASON,
                winner_color,
                _rating_delta(white_session, white_new_elo),
                _rating_delta(black_session, black_new_elo),
            )

            # Persist while sessions still hold their pre-game ELO, before the
            # overwrite below turns before-values into after-values.
            await self._persist_forfeit(outcome, white_session, white_new_elo, black_session, black_new_elo)

            await self._database.update_elo(outcome.winner_session.username, outcome.new_winner_elo)
            await self._database.update_elo(outcome.loser_session.username, outcome.new_loser_elo)
            outcome.winner_session.elo = outcome.new_winner_elo
            outcome.loser_session.elo = outcome.new_loser_elo

        self.remove_participant(disconnected_session)
        await self.stop()
        # A forfeit ends the game by writing straight to GameState, which
        # publishes nothing — so this path has to announce its own expiry.
        self._trigger_expiry()

    @staticmethod
    def _forfeit_outcome_by_seat(outcome: ForfeitOutcome) -> Tuple[Any, int, Any, int]:
        """Reframe a forfeit's winner/loser outcome by White/Black seat.

        The game-end frame speaks in seats (matching the natural-end path),
        while `ForfeitOutcome` speaks in winner/loser — so this bridges the two.

        Returns (white_session, white_new_elo, black_session, black_new_elo).
        """
        if outcome.winner_session.color == consts.COLOR_WHITE:
            return outcome.winner_session, outcome.new_winner_elo, outcome.loser_session, outcome.new_loser_elo
        return outcome.loser_session, outcome.new_loser_elo, outcome.winner_session, outcome.new_winner_elo

    def _on_game_ended(self, event: GameEndedEvent) -> None:
        """GameEndedEvent handler: settle ELO for a natural result, then expire the room.

        Called from inside EventBus dispatch, mid-tick — same constraint
        _trigger_expiry itself is under, so the async database write is
        deferred onto a task of its own rather than awaited inline. Scheduled
        independently of expiry so a database-less room (tests, ad-hoc
        construction) still reaps normally with nothing to persist.
        """
        loop = self._resolve_loop()
        if loop is not None:
            self._elo_settlement_task = loop.create_task(
                self._settle_elo_for_game_end(event.reason, event.winner)
            )
        self._trigger_expiry()

    async def _settle_elo_for_game_end(self, reason: str, winner_color: Optional[str]) -> None:
        """Broadcast the rated game-end frame, then persist ELO for a game
        that ended on its own merits (not a forfeit).

        Mirrors _apply_forfeit's persistence shape: gated on a database
        existing, a no-op whenever either seat is a bot. The broadcast happens
        before either database write, and both happen inside this task's first
        synchronous stretch — ahead of the sibling expiry task _on_game_ended
        also schedules — so the rating a player sees is never stale by the
        time the room is reaped.
        """
        outcome = (
            self._domain.compute_game_end_outcome(winner_color)
            if self._database is not None
            else None
        )
        if outcome is None:
            return

        await self._broadcast_game_ended(
            reason,
            winner_color,
            _rating_delta(outcome.white_session, outcome.new_white_elo),
            _rating_delta(outcome.black_session, outcome.new_black_elo),
        )

        # Persist while sessions still hold their pre-game ELO, before the
        # overwrite below turns before-values into after-values.
        await self._persist_natural_end(outcome, reason, winner_color)

        await self._database.update_elo(outcome.white_session.username, outcome.new_white_elo)
        await self._database.update_elo(outcome.black_session.username, outcome.new_black_elo)
        outcome.white_session.elo = outcome.new_white_elo
        outcome.black_session.elo = outcome.new_black_elo

    async def _persist_natural_end(
        self, outcome: GameEndOutcome, reason: str, winner_color: Optional[str]
    ) -> None:
        """Save a game that ended on its own merits, if persistence is wired.

        Runs while both sessions still hold their pre-game rating, so
        `.elo` supplies the before-values and the outcome's new ratings the
        after-values.
        """
        if self._persistence_service is None:
            return
        game_result = GameResult(
            room_id=self.room_id,
            white_player_id=outcome.white_session.user_id,
            black_player_id=outcome.black_session.user_id,
            winner_id=self._winner_id_for(outcome.white_session, outcome.black_session, winner_color),
            result=reason,
            white_elo_before=outcome.white_session.elo,
            white_elo_after=outcome.new_white_elo,
            black_elo_before=outcome.black_session.elo,
            black_elo_after=outcome.new_black_elo,
            started_at=self._started_at or datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            moves=self._collect_persisted_moves(),
        )
        await self._persistence_service.persist_game(game_result)

    async def _persist_forfeit(
        self,
        outcome: ForfeitOutcome,
        white_session: Any,
        white_new_elo: int,
        black_session: Any,
        black_new_elo: int,
    ) -> None:
        """Save a game ended by disconnection timeout, if persistence is wired.

        The seat framing is already resolved by the caller; the winner comes
        from the forfeit outcome so a draw is impossible on this path.
        """
        if self._persistence_service is None:
            return
        game_result = GameResult(
            room_id=self.room_id,
            white_player_id=white_session.user_id,
            black_player_id=black_session.user_id,
            winner_id=outcome.winner_session.user_id,
            result=_FORFEIT_RESULT,
            white_elo_before=white_session.elo,
            white_elo_after=white_new_elo,
            black_elo_before=black_session.elo,
            black_elo_after=black_new_elo,
            started_at=self._started_at or datetime.now(timezone.utc),
            ended_at=datetime.now(timezone.utc),
            moves=self._collect_persisted_moves(),
        )
        await self._persistence_service.persist_game(game_result)

    @staticmethod
    def _winner_id_for(
        white_session: Any, black_session: Any, winner_color: Optional[str]
    ) -> Optional[int]:
        """Map a winning color onto the winning seat's user id, or None for a draw."""
        if winner_color == consts.COLOR_WHITE:
            return white_session.user_id
        if winner_color == consts.COLOR_BLACK:
            return black_session.user_id
        return None

    def _collect_persisted_moves(self) -> List[PersistedMove]:
        """Decompose this game's move log into database-ready move rows."""
        service = self._domain.service
        if service is None:
            return []
        return persisted_moves_from_log(service.get_moves())

    async def _broadcast_game_ended(
        self,
        reason: str,
        winner: Optional[str],
        white_rating: Dict[str, int],
        black_rating: Dict[str, int],
    ) -> None:
        """Send both seats the rated game-end frame."""
        message = build_game_ended_message(reason, winner, white_rating, black_rating)
        for session in (self._domain.white_player, self._domain.black_player):
            if session is not None:
                await session.send(message)

    def _trigger_expiry(self) -> None:
        """Hand this room to its reaper exactly once, off the current call stack.

        Reached from inside EventBus dispatch, which runs part-way through an
        AsyncGameRunner tick — inside the very task `stop()` must await. Reaping
        inline would leave that task awaiting itself, so teardown is deferred
        onto a task of its own and this returns immediately.
        """
        if self._expired or self._on_room_expired is None:
            return

        loop = self._resolve_loop()
        if loop is None:
            # Without a loop there is nothing to reap: the runner and bot
            # driver a reap exists to cancel could not have been started either.
            _LOGGER.debug("Room %s ended with no event loop to reap it on", self._domain.room_id)
            return

        self._expired = True
        self._expiry_task = loop.create_task(self._notify_expired())

    def _resolve_loop(self) -> Optional[asyncio.AbstractEventLoop]:
        """Find the loop teardown should run on, preferring the injected one."""
        if self._loop is not None:
            return self._loop
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    async def _notify_expired(self) -> None:
        """Invoke the injected reaper, accepting a sync or async callback.

        Failures are logged rather than raised: this runs detached, so an
        escaping exception would surface only as an unretrieved-task warning
        long after the room it describes is gone.
        """
        try:
            outcome = self._on_room_expired(self._domain.room_id)
            if inspect.isawaitable(outcome):
                await outcome
        except Exception as exc:
            _LOGGER.exception("Reaping room %s failed: %s", self._domain.room_id, exc)

    def add_player(self, session: Any) -> RoomRole:
        """Assign player session to White or Black slot.

        Raises:
            ValueError: If both player slots are occupied.
        """
        was_initialized = self._domain.service is not None
        role = self._domain.add_player(session)
        if not was_initialized and self._domain.service is not None:
            self._wire_infrastructure_after_init()
        return role

    def add_bot_opponent(
        self,
        username: str = DEFAULT_BOT_USERNAME,
        move_interval_seconds: float = DEFAULT_BOT_MOVE_INTERVAL_SECONDS,
    ) -> BotPlayerAdapter:
        """Seat an automated opponent in the room's one free seat and wire it to play.

        The bot's `RandomBotInputSource` must be built against the *same* engine
        core the room initialized (identical board/state repositories), which
        only exists once seating the bot completes the room — hence the driver is
        attached after `add_player`, not before.

        Raises:
            ValueError: If the room does not have exactly one seat free.
        """
        if self._domain.is_full or (
            self._domain.white_player is None and self._domain.black_player is None
        ):
            raise ValueError("A bot opponent needs exactly one occupied seat to face")

        bot = BotPlayerAdapter(username=username)
        self.add_player(bot)
        self._attach_bot_driver(bot, move_interval_seconds)
        return bot

    def _attach_bot_driver(self, bot: BotPlayerAdapter, move_interval_seconds: float) -> None:
        if self._core is None or self._domain.config is None or self._runner is None:
            raise RuntimeError("Cannot attach a bot driver before the room's game is initialized")

        input_source = build_random_bot(bot.color, self._core, self._domain.config)
        bot.attach_input_source(input_source)
        self._bot_driver = BotDriver(
            input_source=input_source,
            submit_command=self._runner.submit_command,
            is_game_over=self._is_game_over,
            move_interval_seconds=move_interval_seconds,
            bot_name=bot.username,
        )

    def _is_game_over(self) -> bool:
        if self._core is None:
            return True
        return self._core.state_repo.get_state().game_over

    def add_viewer(self, session: Any) -> None:
        """Add a spectator to the room."""
        self._domain.add_viewer(session)
        if self._broadcast_observer is not None:
            self._broadcast_observer.add_recipient(session)

    def remove_participant(self, session: Any) -> None:
        self._domain.remove_participant(session)
        if self._broadcast_observer is not None:
            self._broadcast_observer.remove_recipient(session)

    def _wire_infrastructure_after_init(self) -> None:
        """Attach network broadcast and the tick runner once the room's game initializes."""
        self._started_at = datetime.now(timezone.utc)
        core = self._domain.core
        self._core = core
        core.event_bus.subscribe(self._lifecycle_observer, GameEndedEvent)

        self._broadcast_observer = NetworkBroadcastObserver(loop=self._loop)
        core.event_bus.subscribe(self._broadcast_observer)
        if self._domain.white_player:
            self._broadcast_observer.add_recipient(self._domain.white_player)
        if self._domain.black_player:
            self._broadcast_observer.add_recipient(self._domain.black_player)

        self._runner = AsyncGameRunner(
            engine=self._domain.engine,
            on_tick=self._on_tick,
        )

    async def start(self) -> None:
        """Start the background tick runner, and the bot's move loop if one is seated."""
        if self._runner and not self._runner.running:
            await self._runner.start()
        if self._bot_driver and not self._bot_driver.running:
            await self._bot_driver.start()

    async def stop(self) -> None:
        """Stop every background task this room owns and set state to FINISHED.

        Safe to call from inside the forfeit callback: the expiring countdown
        has already removed itself from the handler by then, so cancel_all()
        never cancels the task currently awaiting this coroutine.

        The bot driver stops before the runner, so no command can be queued
        against a runner that is already draining its last tick.

        Each collaborator is dropped as it stops, both so a reaped room stops
        pinning its runner's command queue and engine, and so the whole method
        is re-entrant — a room stopped twice (forfeit first, then the reaper)
        finds nothing left to cancel on the second pass.

        Must not be awaited from inside the runner's own tick: `AsyncGameRunner.
        stop()` awaits that task, so an inline call would await itself. See
        `_trigger_expiry`, which defers teardown for exactly this reason.
        """
        self._disconnect_handler.cancel_all()
        if self._bot_driver is not None:
            await self._bot_driver.stop()
            self._bot_driver = None
        if self._runner is not None:
            await self._runner.stop()
            self._runner = None
        self._domain.mark_finished()

    async def handle_move(self, session: Any, from_sq: str, to_sq: str) -> Result[None, str]:
        """Process an algebraic move request directly against the GameEngine.

        Optimization A: Converts algebraic squares to Position(row, col) and calls
        self._engine.request_move(src, dst) directly, bypassing pixel coordinates.

        Returns a failed Result rather than raising, so a spectator's move frame
        or a malformed square is answered with an error and leaves the socket
        open instead of tearing down the connection.
        """
        try:
            src_pos, dst_pos = AlgebraicParser.parse_move(from_sq, to_sq)
        except ValueError as err:
            return Result.fail(str(err))

        result = self._domain.handle_move(session, from_sq, src_pos, dst_pos)
        if not result.is_ok:
            return result

        await self._broadcast_state()
        return result

    async def _on_tick(self) -> None:
        """Callback invoked by AsyncGameRunner on every tick."""
        await self._broadcast_state()

    async def _broadcast_state(self) -> None:
        service = self._domain.service
        if service is None:
            return

        snapshot = service.get_snapshot()
        if snapshot is None:
            return

        state_data = SnapshotSerializer.serialize(snapshot)
        msg = build_game_state_message(state_data)

        recipients = []
        if self._domain.white_player:
            recipients.append(self._domain.white_player)
        if self._domain.black_player:
            recipients.append(self._domain.black_player)
        recipients.extend(self._domain.viewers)

        for rec in recipients:
            try:
                if hasattr(rec, "send"):
                    await rec.send(msg)
                elif hasattr(rec, "send_message"):
                    await rec.send_message(msg)
            except Exception as exc:
                _LOGGER.warning("Failed state broadcast in room %s: %s", self._domain.room_id, exc)
