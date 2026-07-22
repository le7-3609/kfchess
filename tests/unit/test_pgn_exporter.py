"""Golden-string tests for the variant PGN exporter."""

from server.application.game_query_service import GameReplay, ReplayMove
from server.application.pgn_exporter import to_pgn


def _replay(*, winner_id=1, result="checkmate", moves=None):
    return GameReplay(
        game_id=1,
        room_id="ROOM01",
        white_player_id=1,
        black_player_id=2,
        white_username="white",
        black_username="black",
        winner_id=winner_id,
        result=result,
        white_elo_before=1200,
        white_elo_after=1216,
        black_elo_before=1200,
        black_elo_after=1184,
        started_at="2026-01-01T00:00:00+00:00",
        ended_at="2026-01-01T00:05:00+00:00",
        moves=moves or [],
    )


def test_white_win_full_golden_string():
    moves = [
        ReplayMove(1, "e2", "e4", "P", "white", None, 500.0),
        ReplayMove(2, "d1", "h5", "Q", "white", "p", 1200.0),
    ]
    pgn = to_pgn(_replay(moves=moves))

    assert pgn == (
        '[Event "Kung Fu Chess"]\n'
        '[Date "2026.01.01"]\n'
        '[White "white"]\n'
        '[Black "black"]\n'
        '[Result "1-0"]\n'
        '[WhiteElo "1200"]\n'
        '[BlackElo "1200"]\n'
        '[Variant "Kung Fu Chess"]\n'
        '[Termination "checkmate"]\n'
        "\n"
        "1. e2-e4 {[%emt 0.5]} 2. Qd1xh5 {[%emt 1.2]} 1-0\n"
    )


def test_result_tokens_by_outcome():
    assert '[Result "1-0"]' in to_pgn(_replay(winner_id=1))
    assert '[Result "0-1"]' in to_pgn(_replay(winner_id=2))
    draw = to_pgn(_replay(winner_id=None, result="stalemate"))
    assert '[Result "1/2-1/2"]' in draw
    assert draw.rstrip().endswith("1/2-1/2")


def test_capture_uses_x_separator():
    moves = [ReplayMove(1, "f3", "e5", "N", "white", "p", 3200.0)]
    assert "1. Nf3xe5 {[%emt 3.2]}" in to_pgn(_replay(moves=moves))


def test_pawn_has_no_piece_letter():
    moves = [ReplayMove(1, "e2", "e4", "P", "white", None, 0.0)]
    assert "1. e2-e4 {[%emt 0.0]}" in to_pgn(_replay(moves=moves))


def test_empty_game_is_just_result():
    pgn = to_pgn(_replay(moves=[]))
    assert pgn.rstrip().endswith("\n1-0") or pgn.rstrip().splitlines()[-1] == "1-0"


def test_unparseable_date_falls_back_to_placeholder():
    replay = _replay()
    object.__setattr__(replay, "started_at", "not-a-date")
    assert '[Date "????.??.??"]' in to_pgn(replay)
