"""Unit tests for the pure domain GameRoom's fail-fast invariants.

Seating, lifecycle, and move-authorization behavior is covered end-to-end by
tests/unit/test_game_room.py against the infrastructure room that composes
this aggregate.
"""

import pytest

from server.domain.room.game_room import GameRoom


def test_rejects_an_empty_room_id():
    with pytest.raises(ValueError):
        GameRoom(room_id="")


def test_rejects_a_blank_room_id():
    with pytest.raises(ValueError):
        GameRoom(room_id="   ")


class _MockSession:
    def __init__(self, username: str, elo: int = 1200, is_bot: bool = False):
        self.username = username
        self.elo = elo
        self.is_bot = is_bot
        self._color = None

    def assign_color(self, color: str) -> None:
        self._color = color

    @property
    def color(self):
        return self._color


def test_compute_forfeit_outcome_is_none_with_no_opponent():
    room = GameRoom(room_id="R1")
    disconnected = _MockSession("Alice")

    assert room.compute_forfeit_outcome(disconnected, None) is None


def test_compute_forfeit_outcome_is_none_when_a_bot_is_involved():
    room = GameRoom(room_id="R2")
    human = _MockSession("Alice")
    bot = _MockSession("Bot", is_bot=True)

    assert room.compute_forfeit_outcome(human, bot) is None
    assert room.compute_forfeit_outcome(bot, human) is None


def test_compute_forfeit_outcome_rates_a_human_vs_human_forfeit():
    room = GameRoom(room_id="R3")
    winner = _MockSession("Winner", elo=1200)
    loser = _MockSession("Loser", elo=1200)

    outcome = room.compute_forfeit_outcome(loser, winner)

    assert outcome is not None
    assert outcome.winner_session is winner
    assert outcome.loser_session is loser
    assert outcome.new_winner_elo > 1200
    assert outcome.new_loser_elo < 1200
