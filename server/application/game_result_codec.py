"""Game-result codec — a finished game as a broker message and back.

Layer: application (server/application)
Owns: the wire shape of a `game.finished` message and its round trip.
Must not own: when a game finishes, who publishes it, or how it is written.

**Why a codec rather than passing the DTO.** The publisher and the consumer are
different processes, so the object has to survive JSON. `GameResult` and
`PersistedMove` are frozen dataclasses of plain values, which makes the encoding
mechanical — except for the two timestamps, which JSON has no type for and which
must survive as instants rather than as whatever a locale renders them.

**The encoding is the compatibility surface.** A message published by an old
authority may be consumed by a new worker during a rollout, so decoding tolerates
unknown fields and refuses only what it genuinely cannot reconstruct — a message
missing `room_id` cannot be made idempotent and must not be written at all.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from server.application.game_result import GameResult, PersistedMove

_LOGGER = logging.getLogger(__name__)

# Bumped when a change to the shape below cannot be read by the previous
# consumer. Present from the first version so there is something to compare
# against later, rather than a field added once it is already too late.
SCHEMA_VERSION = 1

FIELD_VERSION = "version"
FIELD_ROOM_ID = "room_id"
FIELD_MOVES = "moves"


def encode_game_result(result: GameResult) -> Dict[str, Any]:
    """Render a finished game as the message body published to the stream."""
    return {
        FIELD_VERSION: SCHEMA_VERSION,
        FIELD_ROOM_ID: result.room_id,
        "white_player_id": result.white_player_id,
        "black_player_id": result.black_player_id,
        "winner_id": result.winner_id,
        "result": result.result,
        "white_elo_before": result.white_elo_before,
        "white_elo_after": result.white_elo_after,
        "black_elo_before": result.black_elo_before,
        "black_elo_after": result.black_elo_after,
        "started_at": result.started_at.isoformat(),
        "ended_at": result.ended_at.isoformat(),
        FIELD_MOVES: [_encode_move(move) for move in result.moves],
    }


def decode_game_result(payload: Dict[str, Any]) -> Optional[GameResult]:
    """Rebuild a finished game from a message, or None if it is unusable.

    None rather than an exception: the caller is a worker draining a durable
    stream, and a message it can never read must be acknowledged and dropped
    rather than redelivered forever at the head of the queue.
    """
    try:
        return GameResult(
            room_id=payload[FIELD_ROOM_ID],
            white_player_id=int(payload["white_player_id"]),
            black_player_id=int(payload["black_player_id"]),
            winner_id=_optional_int(payload.get("winner_id")),
            result=payload["result"],
            white_elo_before=int(payload["white_elo_before"]),
            white_elo_after=int(payload["white_elo_after"]),
            black_elo_before=int(payload["black_elo_before"]),
            black_elo_after=int(payload["black_elo_after"]),
            started_at=datetime.fromisoformat(payload["started_at"]),
            ended_at=datetime.fromisoformat(payload["ended_at"]),
            moves=[_decode_move(move) for move in payload.get(FIELD_MOVES, [])],
        )
    except (KeyError, TypeError, ValueError) as exc:
        _LOGGER.error(
            "Discarding an unreadable game-finished message for room %r: %s",
            payload.get(FIELD_ROOM_ID), exc,
        )
        return None


def _encode_move(move: PersistedMove) -> Dict[str, Any]:
    return {
        "move_number": move.move_number,
        "from_square": move.from_square,
        "to_square": move.to_square,
        "piece_type": move.piece_type,
        "piece_color": move.piece_color,
        "captured_piece": move.captured_piece,
        "timestamp": move.timestamp,
    }


def _decode_move(fields: Dict[str, Any]) -> PersistedMove:
    return PersistedMove(
        move_number=int(fields["move_number"]),
        from_square=fields["from_square"],
        to_square=fields["to_square"],
        piece_type=fields["piece_type"],
        piece_color=fields["piece_color"],
        captured_piece=fields.get("captured_piece"),
        timestamp=float(fields["timestamp"]),
    )


def _optional_int(value: Any) -> Optional[int]:
    """A draw's winner is genuinely absent, not zero."""
    return None if value is None else int(value)


def _decode_moves(payloads: List[Dict[str, Any]]) -> List[PersistedMove]:
    return [_decode_move(fields) for fields in payloads]
