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


def _seated_room(room_id: str, white_elo: int = 1200, black_elo: int = 1200, black_is_bot: bool = False):
    """Build a domain room with both seats filled (which starts the game)."""
    room = GameRoom(room_id=room_id)
    white = _MockSession("Alice", elo=white_elo)
    black = _MockSession("Bob", elo=black_elo, is_bot=black_is_bot)
    room.add_player(white)
    room.add_player(black)
    return room, white, black


def test_compute_game_end_outcome_is_none_before_both_seats_are_filled():
    room = GameRoom(room_id="G0")
    room.add_player(_MockSession("Alice"))

    assert room.compute_game_end_outcome("w") is None


def test_compute_game_end_outcome_is_none_when_a_bot_is_involved():
    room, white, bot = _seated_room("G1", black_is_bot=True)

    assert room.compute_game_end_outcome("w") is None
    assert room.compute_game_end_outcome("b") is None
    assert room.compute_game_end_outcome(None) is None


def test_compute_game_end_outcome_rates_a_decisive_white_win():
    room, white, black = _seated_room("G2")

    outcome = room.compute_game_end_outcome("w")

    assert outcome is not None
    assert outcome.white_session is white
    assert outcome.black_session is black
    assert outcome.new_white_elo > 1200
    assert outcome.new_black_elo < 1200


def test_compute_game_end_outcome_rates_a_decisive_black_win():
    room, white, black = _seated_room("G3")

    outcome = room.compute_game_end_outcome("b")

    assert outcome is not None
    assert outcome.new_black_elo > 1200
    assert outcome.new_white_elo < 1200


def test_compute_game_end_outcome_rates_a_draw_toward_the_underdog():
    room, white, black = _seated_room("G4", white_elo=1400, black_elo=1200)

    outcome = room.compute_game_end_outcome(None)

    assert outcome is not None
    # The favorite (white) was expected to score above 0.5, so a draw pulls
    # its rating down; the underdog (black) gains for over-performing.
    assert outcome.new_white_elo < 1400
    assert outcome.new_black_elo > 1200


def test_compute_game_end_outcome_is_none_for_an_unrecognized_winner_color():
    room, white, black = _seated_room("G5")

    assert room.compute_game_end_outcome("purple") is None
