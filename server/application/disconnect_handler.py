"""Disconnect state machine & reconnection handler.

Layer: application (server/application)
Owns: 30-second countdown timer on player disconnect, opponent countdown notifications,
forfeit on timeout, and full GameSnapshot synchronization on reconnection.
Must not own: lower-level WebSocket network transport.

Application rather than infrastructure: deciding that a seat forfeits after the
countdown elapses is game policy, and it notifies both sides of the outcome.
The timer is merely the mechanism it uses to enforce that policy.

Optimization C: State Synchronization Post-Disconnection. On successful reconnection,
the server serializes and transmits a comprehensive GameSnapshot representing total
current state (positions, in-flight movements, per-piece cooldowns, scores, clock_ms)
as a single atomic payload, forcing a clean overwrite of client state.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, Optional

from server.application.dtos.frame_fields import (
    FIELD_COUNTDOWN_SECONDS,
    FIELD_REASON,
    FIELD_SECONDS_REMAINING,
    FIELD_TYPE,
    FIELD_USERNAME,
    FORFEIT_REASON_OPPONENT_TIMEOUT,
    GAME_END_REASON_DISCONNECTION_TIMEOUT,
)
from server.application.dtos.network_frames import (
    MSG_COUNTDOWN_TICK,
    MSG_FORFEIT_VICTORY,
    MSG_OPPONENT_DISCONNECTED,
    MSG_OPPONENT_RECONNECTED,
)
from server.application.dtos.response_frames import build_game_state_message
from server.application.dtos.protocol_mapper import SnapshotSerializer

_LOGGER = logging.getLogger(__name__)

DEFAULT_DISCONNECT_TIMEOUT_SECONDS = 30


class DisconnectHandler:
    """Manages disconnection countdown, reconnection, and state synchronization."""

    def __init__(
        self,
        game_room: Any,
        timeout_seconds: int = DEFAULT_DISCONNECT_TIMEOUT_SECONDS,
        on_forfeit: Optional[Callable[[Any, Any], Awaitable[None]]] = None,
    ) -> None:
        self._game_room = game_room
        self._timeout_seconds = timeout_seconds
        self._on_forfeit = on_forfeit
        self._countdown_tasks: Dict[Any, asyncio.Task] = {}

    def is_disconnected(self, session: Any) -> bool:
        return session in self._countdown_tasks

    def cancel_all(self) -> None:
        """Cancel any in-flight disconnect countdowns, e.g. on room teardown."""
        for task in self._countdown_tasks.values():
            task.cancel()
        self._countdown_tasks.clear()

    def handle_disconnect(self, disconnected_session: Any, opponent_session: Optional[Any]) -> None:
        """Start 30-second forfeit countdown when player drops connection."""
        if disconnected_session in self._countdown_tasks:
            return

        disconnected_session.disconnect()
        _LOGGER.info(
            "Player %s disconnected in room %s. Starting %ds countdown.",
            disconnected_session.username, self._game_room.room_id, self._timeout_seconds
        )

        task = asyncio.create_task(self._countdown_loop(disconnected_session, opponent_session))
        self._countdown_tasks[disconnected_session] = task

    async def handle_reconnect(self, session: Any, new_websocket: Any) -> bool:
        """Rebind a new WebSocket to an existing session and perform full state sync.

        Optimization C: Serializes and transmits a complete GameSnapshot.
        """
        task = self._countdown_tasks.pop(session, None)
        if task is not None:
            task.cancel()

        session.reconnect(new_websocket)
        _LOGGER.info("Player %s reconnected to room %s", session.username, self._game_room.room_id)

        # Optimization C: Push complete GameSnapshot for full state restoration
        if self._game_room.service:
            snapshot = self._game_room.service.get_snapshot()
            if snapshot is not None:
                serialized = SnapshotSerializer.serialize(snapshot)
                msg = build_game_state_message(serialized)
                await session.send(msg)

        # Notify opponent of reconnection
        opponent = self._get_opponent(session)
        if opponent and opponent.connected:
            await opponent.send({FIELD_TYPE: MSG_OPPONENT_RECONNECTED, FIELD_USERNAME: session.username})

        return True

    async def _countdown_loop(self, disconnected_session: Any, opponent_session: Optional[Any]) -> None:
        try:
            # Notify opponent of disconnection
            if opponent_session and opponent_session.connected:
                await opponent_session.send({
                    FIELD_TYPE: MSG_OPPONENT_DISCONNECTED,
                    FIELD_USERNAME: disconnected_session.username,
                    FIELD_COUNTDOWN_SECONDS: self._timeout_seconds,
                })

            for sec_left in range(self._timeout_seconds, 0, -1):
                await asyncio.sleep(1.0)
                if opponent_session and opponent_session.connected:
                    await opponent_session.send({
                        FIELD_TYPE: MSG_COUNTDOWN_TICK,
                        FIELD_SECONDS_REMAINING: sec_left - 1,
                    })

            # Timer expired -> technical forfeit
            _LOGGER.info(
                "Disconnection countdown expired for player %s in room %s. Triggering forfeit.",
                disconnected_session.username, self._game_room.room_id
            )
            self._countdown_tasks.pop(disconnected_session, None)

            if self._game_room.service and self._game_room.service._state_repo:
                state = self._game_room.service._state_repo.get_state()
                winner_color = opponent_session.color if opponent_session else None
                state.end_game(GAME_END_REASON_DISCONNECTION_TIMEOUT, winner=winner_color)

            if opponent_session and opponent_session.connected:
                await opponent_session.send({
                    FIELD_TYPE: MSG_FORFEIT_VICTORY,
                    FIELD_REASON: FORFEIT_REASON_OPPONENT_TIMEOUT,
                })

            if self._on_forfeit:
                await self._on_forfeit(disconnected_session, opponent_session)

        except asyncio.CancelledError:
            _LOGGER.info("Countdown cancelled for player %s (reconnected)", disconnected_session.username)

    def _get_opponent(self, session: Any) -> Optional[Any]:
        if self._game_room.white_player is session:
            return self._game_room.black_player
        elif self._game_room.black_player is session:
            return self._game_room.white_player
        return None
