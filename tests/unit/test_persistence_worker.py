"""Unit tests for the persistence worker and the finished-game codec.

The properties that matter are the ones that decide whether a finished game is
ever lost: a batch that fills is written, a batch that lingers is written anyway,
a shutdown writes what it already accepted, and one bad game does not take the
batch with it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from server.application.game_result import GameResult, PersistedMove
from server.application.game_result_codec import decode_game_result, encode_game_result
from server.application.persistence_worker import PersistenceWorker
from server.domain.coordination.broker import SUBJECT_GAME_FINISHED, InProcessBroker


def _result(room_id: str = "ROOM01", moves: int = 2) -> GameResult:
    started = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
    return GameResult(
        room_id=room_id,
        white_player_id=1,
        black_player_id=2,
        winner_id=1,
        result="checkmate",
        white_elo_before=1200,
        white_elo_after=1216,
        black_elo_before=1200,
        black_elo_after=1184,
        started_at=started,
        ended_at=started + timedelta(seconds=47),
        moves=[
            PersistedMove(i, "e2", "e4", "P", "white", None, float(i * 1000))
            for i in range(1, moves + 1)
        ],
    )


class RecordingPersistence:
    """Stands in for GamePersistenceService, recording what it was asked to save."""

    def __init__(self, fail_on: str = None):
        self.saved = []
        self._fail_on = fail_on

    async def persist_game(self, result):
        if result.room_id == self._fail_on:
            raise RuntimeError("database is unhappy")
        self.saved.append(result)
        return len(self.saved)


def _clock(start: float = 0.0):
    holder = [start]
    return holder, lambda: holder[0]


def test_a_game_result_survives_the_round_trip():
    original = _result(moves=3)

    restored = decode_game_result(encode_game_result(original))

    assert restored == original


def test_a_draws_absent_winner_stays_absent():
    """Decoding it as 0 would attribute the win to whichever user has id 0."""
    original = _result()
    drawn = GameResult(**{**original.__dict__, "winner_id": None})

    restored = decode_game_result(encode_game_result(drawn))

    assert restored.winner_id is None


def test_the_timestamps_survive_as_instants():
    original = _result()

    restored = decode_game_result(encode_game_result(original))

    assert restored.started_at == original.started_at
    assert restored.ended_at == original.ended_at
    assert restored.started_at.tzinfo is not None, "a naive instant is ambiguous across regions"


def test_an_unreadable_message_decodes_to_none_rather_than_raising():
    """A message no consumer can ever read must be droppable, or it redelivers
    forever at the head of the queue and stalls every game behind it."""
    assert decode_game_result({"room_id": "ROOM01"}) is None
    assert decode_game_result({}) is None


def test_an_unknown_field_is_tolerated():
    """An old authority's message must still be readable by a new worker
    mid-rollout, and vice versa."""
    payload = encode_game_result(_result())
    payload["something_added_later"] = True

    assert decode_game_result(payload) is not None


@pytest.mark.asyncio
async def test_a_full_batch_is_written():
    persistence = RecordingPersistence()
    broker = InProcessBroker()
    worker = PersistenceWorker(broker, persistence, batch_size=3)
    await worker.start()

    for i in range(3):
        await broker.publish_durable(SUBJECT_GAME_FINISHED, encode_game_result(_result(f"R{i}")))

    assert [r.room_id for r in persistence.saved] == ["R0", "R1", "R2"]
    assert worker.pending_count == 0


@pytest.mark.asyncio
async def test_a_partial_batch_waits_rather_than_being_written_immediately():
    """Otherwise batching buys nothing: every game would be its own round trip."""
    persistence = RecordingPersistence()
    broker = InProcessBroker()
    worker = PersistenceWorker(broker, persistence, batch_size=10)
    await worker.start()

    await broker.publish_durable(SUBJECT_GAME_FINISHED, encode_game_result(_result()))

    assert persistence.saved == []
    assert worker.pending_count == 1
    await worker.stop()


@pytest.mark.asyncio
async def test_a_lingering_batch_is_written_once_it_is_old_enough():
    """A finished game is a rating a player is waiting to see, so the wait must
    be bounded by time and not only by how busy the fleet happens to be."""
    holder, read = _clock()
    persistence = RecordingPersistence()
    worker = PersistenceWorker(
        InProcessBroker(), persistence, batch_size=10, linger_seconds=1.0, clock_fn=read
    )
    await worker._on_message(encode_game_result(_result()))

    assert not worker._batch_is_stale()
    holder[0] = 1.0
    assert worker._batch_is_stale()

    await worker.flush()
    assert len(persistence.saved) == 1


@pytest.mark.asyncio
async def test_stopping_writes_what_was_already_accepted():
    """Those games were acknowledged to the broker, so nothing will redeliver
    them — exiting without writing them is the one way a game is truly lost."""
    persistence = RecordingPersistence()
    broker = InProcessBroker()
    worker = PersistenceWorker(broker, persistence, batch_size=100)
    await worker.start()
    await broker.publish_durable(SUBJECT_GAME_FINISHED, encode_game_result(_result("LAST01")))

    await worker.stop()

    assert [r.room_id for r in persistence.saved] == ["LAST01"]


@pytest.mark.asyncio
async def test_one_failing_game_does_not_take_the_batch_with_it():
    """The batch amortises a round trip; it is not a transaction across games
    that share nothing."""
    persistence = RecordingPersistence(fail_on="BAD")
    worker = PersistenceWorker(InProcessBroker(), persistence, batch_size=3)

    for room_id in ("GOOD1", "BAD", "GOOD2"):
        await worker._on_message(encode_game_result(_result(room_id)))

    assert [r.room_id for r in persistence.saved] == ["GOOD1", "GOOD2"]
    assert worker.written_count == 2


@pytest.mark.asyncio
async def test_an_undecodable_message_is_dropped_without_stalling_the_batch():
    persistence = RecordingPersistence()
    worker = PersistenceWorker(InProcessBroker(), persistence, batch_size=2)

    await worker._on_message({"room_id": "TRUNCATED"})
    await worker._on_message(encode_game_result(_result("FINE01")))

    assert worker.pending_count == 1, "the unreadable message occupied no slot"
    await worker.flush()
    assert [r.room_id for r in persistence.saved] == ["FINE01"]


@pytest.mark.asyncio
async def test_a_redelivered_game_is_written_twice_and_the_database_deduplicates():
    """At-least-once is the contract, not a defect. The worker does not
    deduplicate — `ON CONFLICT (room_id) DO NOTHING` does, which is why Step 3's
    idempotency was a prerequisite for this step."""
    persistence = RecordingPersistence()
    worker = PersistenceWorker(InProcessBroker(), persistence, batch_size=2)

    await worker._on_message(encode_game_result(_result("SAME01")))
    await worker._on_message(encode_game_result(_result("SAME01")))

    assert [r.room_id for r in persistence.saved] == ["SAME01", "SAME01"]
