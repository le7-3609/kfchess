"""ELO rating calculator — pure mathematical functions (no side effects).

Layer: domain (server/domain/matchmaking)
Owns: the standard ELO formula for computing new ratings after a match.
Must not own: database persistence, player identity, or match history.

Standard ELO formula:
    E_A = 1 / (1 + 10^((R_B - R_A) / 400))
    R'_A = R_A + K * (S_A - E_A)

where S_A is 1 for a win, 0 for a loss, 0.5 for a draw.
"""

from typing import Tuple

# Default K-factor: FIDE casual rating.
DEFAULT_K_FACTOR = 20
_ELO_SCALING_BASE = 10
_ELO_SCALING_DIVISOR = 400


def expected_score(player_elo: int, opponent_elo: int) -> float:
    """Compute the expected score E_A for a player against an opponent.

    Returns a float in [0, 1] representing the probability of winning.
    """
    return 1.0 / (1.0 + _ELO_SCALING_BASE ** ((opponent_elo - player_elo) / _ELO_SCALING_DIVISOR))


def calculate_elo(
    winner_elo: int,
    loser_elo: int,
    *,
    draw: bool = False,
    k: int = DEFAULT_K_FACTOR,
) -> Tuple[int, int]:
    """Calculate new ELO ratings after a match.

    Args:
        winner_elo: Current ELO of the winning player (or player A if draw).
        loser_elo: Current ELO of the losing player (or player B if draw).
        draw: If True, the match was a draw (both players score 0.5).
        k: K-factor controlling rating volatility.

    Returns:
        A (new_winner_elo, new_loser_elo) tuple of integers.
    """
    expected_winner = expected_score(winner_elo, loser_elo)
    expected_loser = expected_score(loser_elo, winner_elo)

    if draw:
        actual_winner = 0.5
        actual_loser = 0.5
    else:
        actual_winner = 1.0
        actual_loser = 0.0

    new_winner = round(winner_elo + k * (actual_winner - expected_winner))
    new_loser = round(loser_elo + k * (actual_loser - expected_loser))

    return new_winner, new_loser
