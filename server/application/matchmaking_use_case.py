"""Matchmaking use case — queue membership, pairing, and the bot fallback.

Layer: application (server/application)
Owns: the orchestration that turns a queued player into a seated one — draining
formed pairs, rescuing timed-out players onto a bot, and announcing the seat to
both sides.
Must not own: pairing policy (MatchmakingQueue), room allocation (RoomManager),
or socket transport. Announcements go out through the seat contract, not a
socket this layer holds.
"""

import logging
from typing import Any

from shared.model.game_state import Result

from server.application.dtos import ERROR_ALREADY_SEATED
from server.domain.matchmaking.queue import MatchmakingQueue
from server.domain.room.room_role import RoomRole
from server.infrastructure.services.bot_driver import DEFAULT_BOT_MOVE_INTERVAL_SECONDS
from server.application.dtos.response_frames import (
    build_error_message,
    build_game_start_message,
)
from server.application.room_manager import RoomManager

_LOGGER = logging.getLogger(__name__)


class MatchmakingUseCase:
    """Moves players between the waiting queue and a started game."""

    def __init__(
        self,
        matchmaker: MatchmakingQueue,
        room_manager: RoomManager,
        bot_move_interval_seconds: float = DEFAULT_BOT_MOVE_INTERVAL_SECONDS,
    ) -> None:
        self._matchmaker = matchmaker
        self._room_manager = room_manager
        self._bot_move_interval_seconds = bot_move_interval_seconds

    async def enqueue(self, session: Any) -> Result[None, str]:
        """Enter an authenticated player into ELO-bounded matchmaking.

        Success is silent: the next frame this client sees is `game_start`,
        either from a pairing or from the bot fallback once the queue timeout
        elapses.
        """
        if self._room_manager.find_room_by_session(session) is not None:
            return Result.fail(ERROR_ALREADY_SEATED)
        await self._matchmaker.join_queue(session)
        return Result.ok(None)

    async def cancel(self, session: Any) -> Result[None, str]:
        """Withdraw a waiting player so no pairing can reach them."""
        was_queued = await self._matchmaker.leave_queue(session)
        if not was_queued:
            return Result.fail("Not currently searching for a match")
        return Result.ok(None)

    async def drain_matches(self) -> None:
        """Start a room for every pair the matchmaker can form this cycle."""
        while True:
            pair = await self._matchmaker.try_match()
            if pair is None:
                return
            white_session, black_session = pair
            await self.start_match(white_session, black_session)

    async def drain_timeouts(self) -> None:
        """Give every player who aged out of the queue a bot opponent.

        A timeout is a dead end for the player, not an error: rather than
        evicting them, they are seated against a bot immediately.
        """
        for session in await self._matchmaker.check_timeouts():
            if not session.connected:
                continue
            try:
                await self.start_bot_match(session)
            except Exception as exc:
                _LOGGER.exception("Bot fallback failed for %s: %s", session.username, exc)
                await session.send(build_error_message("Could not start a game against the bot"))

    async def start_match(self, white_session: Any, black_session: Any) -> None:
        """Allocate a room for a matched pair, seat both, and start the game.

        The longest-waiting player takes White, which keeps seating
        deterministic for a given queue order.
        """
        room_id = self._room_manager.create_room(white_session)
        self._room_manager.join_room(room_id, black_session)
        room = self._room_manager.get_room(room_id)

        await white_session.send(
            build_game_start_message(RoomRole.WHITE_PLAYER.value, black_session.username, room_id)
        )
        await black_session.send(
            build_game_start_message(RoomRole.BLACK_PLAYER.value, white_session.username, room_id)
        )

        await room.start()
        _LOGGER.info(
            "Match started in room %s: %s (W) vs %s (B)",
            room_id, white_session.username, black_session.username,
        )

    async def start_bot_match(self, session: Any) -> None:
        """Seat a queue-timeout player as White against an automated opponent."""
        room_id = self._room_manager.create_room(session)
        room = self._room_manager.get_room(room_id)
        bot = room.add_bot_opponent(move_interval_seconds=self._bot_move_interval_seconds)

        await session.send(
            build_game_start_message(RoomRole.WHITE_PLAYER.value, bot.username, room_id)
        )
        await room.start()
        _LOGGER.info(
            "Bot fallback started in room %s for %s vs %s", room_id, session.username, bot.username
        )
