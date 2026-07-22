"""Unit tests for GamePersistenceService and the move-log conversion."""

from datetime import datetime, timezone

import pytest

from shared.io.moves_log import MoveLogEntry
from server.application.capture_log import CaptureRecord
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


def _capture(at_ms, square, piece_type, captor_from, captor_to, color="b"):
    return CaptureRecord(
        at_ms=at_ms, square=square, piece_type=piece_type, color=color,
        captor_from=captor_from, captor_to=captor_to,
    )


def test_persisted_moves_annotates_arrival_capture():
    entries = [
        MoveLogEntry(color="w", notation="Pe2-e4", time_ms=500),
        MoveLogEntry(color="w", notation="Qd1-h5", time_ms=1200),
    ]
    # The queen arrives on h5 at 1200 and takes the pawn standing there.
    captures = [_capture(1200, "h5", "p", captor_from="d1", captor_to="h5")]

    rows = persisted_moves_from_log(entries, captures)

    assert rows[0].captured_piece is None
    assert rows[1].captured_piece == "p"


def test_persisted_moves_annotates_mid_transit_collision_capture():
    entries = [MoveLogEntry(color="w", notation="Ra1-a8", time_ms=7000)]
    # Collision at a4 at 3000, long before the rook's own arrival at 7000 —
    # neither the capture instant nor its square matches the move row directly.
    captures = [_capture(3000, "a4", "n", captor_from="a1", captor_to="a8")]

    rows = persisted_moves_from_log(entries, captures)

    assert rows[0].captured_piece == "n"


def test_persisted_moves_annotates_en_passant_on_the_bypassed_square():
    entries = [MoveLogEntry(color="w", notation="Pd5-e6", time_ms=900)]
    # En passant removes the pawn from e5, not from the mover's destination e6.
    captures = [_capture(900, "e5", "p", captor_from="d5", captor_to="e6")]

    rows = persisted_moves_from_log(entries, captures)

    assert rows[0].captured_piece == "p"


def test_persisted_moves_ignores_capture_whose_move_never_completed():
    entries = [MoveLogEntry(color="w", notation="Pe2-e4", time_ms=500)]
    # Airborne strike: the captor is a jump in place, which publishes no move.
    captures = [_capture(300, "c6", "N", captor_from="c6", captor_to="c6")]

    rows = persisted_moves_from_log(entries, captures)

    assert len(rows) == 1
    assert rows[0].captured_piece is None


def test_persisted_moves_records_friendly_fire_capture():
    entries = [MoveLogEntry(color="w", notation="Rd1-d4", time_ms=700)]
    # Same-color capture: the removed piece is also white.
    captures = [_capture(700, "d4", "P", captor_from="d1", captor_to="d4", color="w")]

    rows = persisted_moves_from_log(entries, captures)

    assert rows[0].captured_piece == "P"


def test_persisted_moves_joins_multiple_captures_on_one_transit():
    entries = [MoveLogEntry(color="w", notation="Ra1-a8", time_ms=7000)]
    # A mid-path collision at 3000 and then an arrival capture at 7000,
    # both taken by the same transit.
    captures = [
        _capture(3000, "a4", "n", captor_from="a1", captor_to="a8"),
        _capture(7000, "a8", "r", captor_from="a1", captor_to="a8"),
    ]

    rows = persisted_moves_from_log(entries, captures)

    assert rows[0].captured_piece == "n,r"


def test_persisted_moves_attributes_capture_to_the_right_repeat_transit():
    # The same (from, to) transit happens twice; only the second one captured.
    entries = [
        MoveLogEntry(color="w", notation="Ra1-a4", time_ms=1000),
        MoveLogEntry(color="w", notation="Ra4-a1", time_ms=3000),
        MoveLogEntry(color="w", notation="Ra1-a4", time_ms=6000),
    ]
    captures = [_capture(5500, "a3", "p", captor_from="a1", captor_to="a4")]

    rows = persisted_moves_from_log(entries, captures)

    assert rows[0].captured_piece is None  # the earlier transit stays clean
    assert rows[2].captured_piece == "p"


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
