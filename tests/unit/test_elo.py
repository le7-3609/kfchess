"""Unit tests for the ELO rating calculator."""

import pytest
from server.domain.matchmaking.elo import calculate_elo, expected_score


def test_expected_score_equal_ratings():
    # Two equal players should both have expected score of 0.5
    exp_a = expected_score(1200, 1200)
    exp_b = expected_score(1200, 1200)
    assert pytest.approx(exp_a, 0.001) == 0.5
    assert pytest.approx(exp_b, 0.001) == 0.5


def test_expected_score_higher_rating():
    # 1400 vs 1200 player should have > 0.5 expectation
    exp_strong = expected_score(1400, 1200)
    exp_weak = expected_score(1200, 1400)
    assert exp_strong > 0.7
    assert exp_weak < 0.3
    assert pytest.approx(exp_strong + exp_weak, 0.001) == 1.0


def test_calculate_elo_equal_ratings_win():
    # 1200 vs 1200 win with K=20 -> Winner gets +10, loser gets -10
    new_w, new_l = calculate_elo(1200, 1200, draw=False, k=20)
    assert new_w == 1210
    assert new_l == 1190


def test_calculate_elo_draw_equal_ratings():
    # 1200 vs 1200 draw -> ratings do not change
    new_w, new_l = calculate_elo(1200, 1200, draw=True, k=20)
    assert new_w == 1200
    assert new_l == 1200


def test_calculate_elo_upset_win():
    # Lower rated player (1000) beats higher rated player (1400)
    new_w, new_l = calculate_elo(1000, 1400, draw=False, k=20)
    # Underdog win gains significantly more points
    assert new_w > 1015
    assert new_l < 1385
