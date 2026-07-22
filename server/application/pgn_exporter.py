"""Variant PGN export — a pure GameReplay -> PGN string transform.

Layer: application (server/application)
Owns: rendering a completed game as PGN text. Kung Fu Chess has no turns, moves
do not alternate, and friendly fire is legal, so this is explicitly a *variant*
PGN (tagged Variant "Kung Fu Chess"): moves are numbered sequentially in the
order they landed and per-move clock comments carry the real-time pacing.
Must not own: I/O (callers write the string), SQL, or game rules. Being a pure
function keeps it trivially unit-testable against golden strings.
"""

from typing import List, Optional

from server.application.game_query_service import GameReplay, ReplayMove

_EVENT_NAME = "Kung Fu Chess"

# Pawns carry no piece letter in long-algebraic notation, matching standard PGN.
_PAWN = "P"


def to_pgn(replay: GameReplay) -> str:
    """Render *replay* as a variant PGN document."""
    tag_section = "\n".join(_tag_pairs(replay))
    movetext = _movetext(replay.moves, _result_token(replay))
    return f"{tag_section}\n\n{movetext}\n"


def _tag_pairs(replay: GameReplay) -> List[str]:
    """The seven-tag roster plus the variant/rating/termination extensions."""
    pairs = [
        ("Event", _EVENT_NAME),
        ("Date", _pgn_date(replay.started_at)),
        ("White", replay.white_username),
        ("Black", replay.black_username),
        ("Result", _result_token(replay)),
        ("WhiteElo", str(replay.white_elo_before)),
        ("BlackElo", str(replay.black_elo_before)),
        ("Variant", _EVENT_NAME),
        ("Termination", replay.result),
    ]
    return [f'[{name} "{value}"]' for name, value in pairs]


def _result_token(replay: GameReplay) -> str:
    """Map the stored winner id onto a PGN result token."""
    if replay.winner_id is None:
        return "1/2-1/2"
    if replay.winner_id == replay.white_player_id:
        return "1-0"
    return "0-1"


def _pgn_date(started_at: str) -> str:
    """ISO timestamp -> PGN 'YYYY.MM.DD', or the unknown-date placeholder."""
    date_part = started_at[:10]
    if len(date_part) == 10 and date_part[4] == "-" and date_part[7] == "-":
        return date_part.replace("-", ".")
    return "????.??.??"


def _movetext(moves: List[ReplayMove], result_token: str) -> str:
    """Numbered long-algebraic moves with per-move clock comments, result last.

    An empty game is just the result token, which is still legal movetext.
    """
    tokens = [f"{move.move_number}. {_render_move(move)}" for move in moves]
    tokens.append(result_token)
    return " ".join(tokens)


def _render_move(move: ReplayMove) -> str:
    """One move as '<piece><from><sep><to> {[%emt <seconds>]}'.

    *sep* is 'x' for a capture, '-' otherwise; the emt comment carries the
    game-clock instant the move landed (seconds), preserving real-time pacing.
    """
    separator = "x" if move.captured_piece else "-"
    san = f"{_piece_prefix(move.piece_type)}{move.from_square}{separator}{move.to_square}"
    return f"{san} {{[%emt {_emt_seconds(move.timestamp)}]}}"


def _piece_prefix(piece_type: Optional[str]) -> str:
    return "" if piece_type == _PAWN else (piece_type or "")


def _emt_seconds(timestamp_ms: float) -> str:
    return f"{timestamp_ms / 1000:.1f}"
