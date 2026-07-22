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

from shared.config.consts import MS_PER_SECOND, PIECE_PAWN

from server.application.game_query_service import GameReplay, ReplayMove

_EVENT_NAME = "Kung Fu Chess"

# PGN seven-tag-roster names plus the variant/rating/termination extensions.
_TAG_EVENT = "Event"
_TAG_DATE = "Date"
_TAG_WHITE = "White"
_TAG_BLACK = "Black"
_TAG_RESULT = "Result"
_TAG_WHITE_ELO = "WhiteElo"
_TAG_BLACK_ELO = "BlackElo"
_TAG_VARIANT = "Variant"
_TAG_TERMINATION = "Termination"

# PGN result tokens.
_RESULT_DRAW = "1/2-1/2"
_RESULT_WHITE_WINS = "1-0"
_RESULT_BLACK_WINS = "0-1"

# ISO 'YYYY-MM-DD' date prefix geometry, used to validate before reformatting.
_ISO_DATE_LENGTH = 10
_ISO_YEAR_MONTH_SEPARATOR_INDEX = 4
_ISO_MONTH_DAY_SEPARATOR_INDEX = 7
_ISO_DATE_SEPARATOR = "-"
_PGN_DATE_SEPARATOR = "."
_PGN_UNKNOWN_DATE = "????.??.??"

_CAPTURE_SEPARATOR = "x"
_QUIET_MOVE_SEPARATOR = "-"


def to_pgn(replay: GameReplay) -> str:
    """Render *replay* as a variant PGN document."""
    tag_section = "\n".join(_tag_pairs(replay))
    movetext = _movetext(replay.moves, _result_token(replay))
    return f"{tag_section}\n\n{movetext}\n"


def _tag_pairs(replay: GameReplay) -> List[str]:
    """The seven-tag roster plus the variant/rating/termination extensions."""
    pairs = [
        (_TAG_EVENT, _EVENT_NAME),
        (_TAG_DATE, _pgn_date(replay.started_at)),
        (_TAG_WHITE, replay.white_username),
        (_TAG_BLACK, replay.black_username),
        (_TAG_RESULT, _result_token(replay)),
        (_TAG_WHITE_ELO, str(replay.white_elo_before)),
        (_TAG_BLACK_ELO, str(replay.black_elo_before)),
        (_TAG_VARIANT, _EVENT_NAME),
        (_TAG_TERMINATION, replay.result),
    ]
    return [f'[{name} "{value}"]' for name, value in pairs]


def _result_token(replay: GameReplay) -> str:
    """Map the stored winner id onto a PGN result token."""
    if replay.winner_id is None:
        return _RESULT_DRAW
    if replay.winner_id == replay.white_player_id:
        return _RESULT_WHITE_WINS
    return _RESULT_BLACK_WINS


def _pgn_date(started_at: str) -> str:
    """ISO timestamp -> PGN 'YYYY.MM.DD', or the unknown-date placeholder."""
    date_part = started_at[:_ISO_DATE_LENGTH]
    is_iso_shaped = (
        len(date_part) == _ISO_DATE_LENGTH
        and date_part[_ISO_YEAR_MONTH_SEPARATOR_INDEX] == _ISO_DATE_SEPARATOR
        and date_part[_ISO_MONTH_DAY_SEPARATOR_INDEX] == _ISO_DATE_SEPARATOR
    )
    if is_iso_shaped:
        return date_part.replace(_ISO_DATE_SEPARATOR, _PGN_DATE_SEPARATOR)
    return _PGN_UNKNOWN_DATE


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
    separator = _CAPTURE_SEPARATOR if move.captured_piece else _QUIET_MOVE_SEPARATOR
    san = f"{_piece_prefix(move.piece_type)}{move.from_square}{separator}{move.to_square}"
    return f"{san} {{[%emt {_emt_seconds(move.timestamp)}]}}"


def _piece_prefix(piece_type: Optional[str]) -> str:
    # Pawns carry no piece letter in long-algebraic notation, matching standard PGN.
    return "" if piece_type == PIECE_PAWN else (piece_type or "")


def _emt_seconds(timestamp_ms: float) -> str:
    return f"{timestamp_ms / MS_PER_SECOND:.1f}"
