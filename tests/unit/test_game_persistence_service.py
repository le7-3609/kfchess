"""Unit tests for GamePersistenceService and the move-log conversion."""

from datetime import datetime, timezone

import pytest

from shared.io.moves_log import MoveLogEntry
from server.application.game_persistence_service import GamePersistenceService
from server.application.game_result import GameResult, persisted_moves_from_log


class _RecordingDatabase:
    def __init__(self, returned_id=7):
        self.calls = []
        self._returned_id = returned_id

    async def save_completed_game(self, game, moves):
        self.calls.append((game, list(moves)))
        return self._returned_id


def _game_result(moves):
    return GameResult(
        room_id="R1",
        white_player_id=1,
        black_player_id=2,
        winner_id=1,
        result="checkmate",
        white_elo_before=1200,
        white_elo_after=1216,
        black_elo_before=1200,
        black_elo_after=1184,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ended_at=datetime(2026, 1, 1, 0, 5, tzinfo=timezone.utc),
        moves=moves,
    )


def test_persisted_moves_from_log_decomposes_notation():
    entries = [
        MoveLogEntry(color="w", notation="Pe2-e4", time_ms=500),
        MoveLogEntry(color="b", notation="Ng8-f6", time_ms=1200),
    ]

    rows = persisted_moves_from_log(entries)

    assert [r.move_number for r in rows] == [1, 2]
    assert rows[0].from_square == "e2" and rows[0].to_square == "e4"
    assert rows[0].piece_type == "P" and rows[0].piece_color == "white"
    assert rows[0].timestamp == 500.0
    assert rows[1].piece_type == "N" and rows[1].piece_color == "black"


def test_persisted_moves_skips_unparseable_and_keeps_numbering_gapless():
    entries = [
        MoveLogEntry(color="w", notation="Pe2-e4", time_ms=100),
        MoveLogEntry(color="w", notation="garbage", time_ms=200),
        MoveLogEntry(color="b", notation="Pe7-e5", time_ms=300),
    ]

    rows = persisted_moves_from_log(entries)

    assert len(rows) == 2
    assert [r.move_number for r in rows] == [1, 2]
    assert [r.to_square for r in rows] == ["e4", "e5"]


@pytest.mark.asyncio
async def test_persist_game_delegates_to_database_and_returns_id():
    db = _RecordingDatabase(returned_id=42)
    service = GamePersistenceService(db)
    moves = persisted_moves_from_log([MoveLogEntry(color="w", notation="Pe2-e4", time_ms=1)])
    game_result = _game_result(moves)

    game_id = await service.persist_game(game_result)

    assert game_id == 42
    assert len(db.calls) == 1
    saved_game, saved_moves = db.calls[0]
    assert saved_game is game_result
    assert saved_moves == moves


@pytest.mark.asyncio
async def test_persist_game_returns_none_when_save_rolls_back():
    class _RollbackDatabase:
        async def save_completed_game(self, game, moves):
            return None

    service = GamePersistenceService(_RollbackDatabase())

    assert await service.persist_game(_game_result([])) is None
