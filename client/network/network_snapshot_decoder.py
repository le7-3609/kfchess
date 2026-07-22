"""Decodes server `game_state` wire payloads into GameSnapshot DTOs (client layer).

Owns: reconstructing the immutable GameSnapshot the renderer consumes from the
JSON-friendly dict the server's SnapshotSerializer (server/application/dtos/protocol_mapper.py) produces.
Must not own: network I/O, GUI state, or game rules.
"""

from typing import Any, Dict

from client.network import protocol
from client.notation.algebraic_notation import parse_square
from shared.view.game_snapshot import GameSnapshot, MovementSnapshot, PieceSnapshot
from shared.view.piece_visual_state import PieceVisualState

# SnapshotSerializer.serialize only puts color/piece_type on the wire for an
# in-flight movement (see server/application/dtos/protocol_mapper.py) because PillowRenderer reads a
# moving piece's visual state from snapshot.pieces, never from
# MovementSnapshot.piece itself — it only uses frm/to/start_ms/arrival_ms to
# interpolate position. These placeholders fill the PieceSnapshot shape
# without inventing data the wire never sends.
_UNTRANSMITTED_STATE_MS = 0


def decode_game_snapshot(state: Dict[str, Any]) -> GameSnapshot:
    """Rebuild a GameSnapshot from a decoded `game_state` message's `state` dict."""
    pieces = {
        parse_square(square): _decode_piece(piece_data)
        for square, piece_data in state[protocol.FIELD_PIECES].items()
    }
    selected_square = state[protocol.FIELD_SELECTED_POS]
    selected_pos = parse_square(selected_square) if selected_square else None

    return GameSnapshot(
        rows=state[protocol.FIELD_ROWS],
        cols=state[protocol.FIELD_COLS],
        pieces=pieces,
        selected_pos=selected_pos,
        legal_move_targets=tuple(parse_square(sq) for sq in state[protocol.FIELD_LEGAL_MOVE_TARGETS]),
        castle_targets=tuple(parse_square(sq) for sq in state[protocol.FIELD_CASTLE_TARGETS]),
        active_movements=tuple(_decode_movement(m) for m in state[protocol.FIELD_ACTIVE_MOVEMENTS]),
        cooldown_positions=tuple(parse_square(sq) for sq in state[protocol.FIELD_COOLDOWN_POSITIONS]),
        clock_ms=state[protocol.FIELD_CLOCK_MS],
        game_over=state[protocol.FIELD_GAME_OVER],
        game_over_reason=state[protocol.FIELD_GAME_OVER_REASON],
        winner=state[protocol.FIELD_WINNER],
    )


def _decode_piece(piece_data: Dict[str, Any]) -> PieceSnapshot:
    return PieceSnapshot(
        color=piece_data[protocol.FIELD_COLOR],
        piece_type=piece_data[protocol.FIELD_PIECE_TYPE],
        has_moved=piece_data[protocol.FIELD_HAS_MOVED],
        can_select=piece_data[protocol.FIELD_CAN_SELECT],
        can_move=piece_data[protocol.FIELD_CAN_MOVE],
        state=PieceVisualState[piece_data[protocol.FIELD_STATE]],
        state_elapsed_millis=piece_data[protocol.FIELD_STATE_ELAPSED_MS],
        state_duration_millis=piece_data[protocol.FIELD_STATE_DURATION_MS],
    )


def _decode_movement(movement_data: Dict[str, Any]) -> MovementSnapshot:
    placeholder_piece = PieceSnapshot(
        color=movement_data[protocol.FIELD_COLOR],
        piece_type=movement_data[protocol.FIELD_PIECE_TYPE],
        has_moved=False,
        can_select=False,
        can_move=False,
        state=PieceVisualState.IDLE,
        state_elapsed_millis=_UNTRANSMITTED_STATE_MS,
        state_duration_millis=_UNTRANSMITTED_STATE_MS,
    )
    return MovementSnapshot(
        frm=parse_square(movement_data[protocol.FIELD_FROM]),
        to=parse_square(movement_data[protocol.FIELD_TO]),
        piece=placeholder_piece,
        start_ms=movement_data[protocol.FIELD_START_MS],
        arrival_ms=movement_data[protocol.FIELD_ARRIVAL_MS],
    )
