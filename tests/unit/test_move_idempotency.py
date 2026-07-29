"""Unit tests for the per-room move idempotency cache."""

from core.model.game_state import Result
from server.application.move_idempotency import MoveIdempotencyCache


def test_an_unseen_id_recalls_nothing():
    assert MoveIdempotencyCache().recall("m1") is None


def test_a_remembered_id_recalls_its_original_result():
    """A retry must be answered with what the first attempt produced, not
    re-executed — in real-time chess a second execution is a second motion."""
    cache = MoveIdempotencyCache()
    original = Result.ok(None)

    cache.remember("m1", original)

    assert cache.recall("m1") is original


def test_a_remembered_failure_is_replayed_as_the_same_failure():
    cache = MoveIdempotencyCache()
    refusal = Result.fail("Spectators cannot move")

    cache.remember("m1", refusal)

    assert cache.recall("m1") is refusal


def test_an_absent_id_is_always_treated_as_new():
    """Idempotency is opt-in: a client that supplies no id keeps the old
    behaviour rather than having every one of its moves collapse into a shared
    null key."""
    cache = MoveIdempotencyCache()

    cache.remember(None, Result.ok(None))
    cache.remember("", Result.ok(None))

    assert cache.recall(None) is None
    assert cache.recall("") is None
    assert len(cache) == 0


def test_the_cache_evicts_the_oldest_entry_beyond_its_capacity():
    """The key is client-supplied, so the map must be bounded; capacity is far
    above any real game's move count, so a legitimate retry always hits."""
    cache = MoveIdempotencyCache(capacity=2)

    cache.remember("m1", Result.ok(None))
    cache.remember("m2", Result.ok(None))
    cache.remember("m3", Result.ok(None))

    assert cache.recall("m1") is None
    assert cache.recall("m2") is not None
    assert cache.recall("m3") is not None
    assert len(cache) == 2


def test_re_remembering_an_id_refreshes_its_position():
    cache = MoveIdempotencyCache(capacity=2)
    cache.remember("m1", Result.ok(None))
    cache.remember("m2", Result.ok(None))

    cache.remember("m1", Result.ok(None))
    cache.remember("m3", Result.ok(None))

    assert cache.recall("m2") is None
    assert cache.recall("m1") is not None
