"""Unit tests for MatchmakingQueue's fail-fast construction invariants.

Queueing/pairing/timeout behavior is covered by tests/unit/test_matchmaker.py.
"""

import pytest

from server.domain.matchmaking.queue import MatchmakingQueue


def test_rejects_a_negative_elo_bound():
    with pytest.raises(ValueError):
        MatchmakingQueue(max_elo_diff=-1)


def test_rejects_a_non_positive_timeout():
    with pytest.raises(ValueError):
        MatchmakingQueue(timeout_seconds=0)

    with pytest.raises(ValueError):
        MatchmakingQueue(timeout_seconds=-5.0)


def test_accepts_a_zero_elo_bound():
    """An exact-rating-only queue is a legitimate, if strict, configuration."""
    mm = MatchmakingQueue(max_elo_diff=0)
    assert mm.queue_length == 0
