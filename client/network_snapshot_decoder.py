"""Decodes server `game_state` wire payloads into GameSnapshot DTOs (client layer).

Owns: reconstructing the immutable GameSnapshot the renderer consumes from the
JSON-friendly dict the server's SnapshotSerializer (server/application/dtos/protocol_mapper.py) produces.
Must not own: network I/O, GUI state, or game rules.
"""

from typing import Any, Dict

from client.algebraic_notation import parse_square
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
        for square, piece_data in state["pieces"].items()
    }
    selected_pos = parse_square(state["selected_pos"]) if state["selected_pos"] else None

    return GameSnapshot(
        rows=state["rows"],
        cols=state["cols"],
        pieces=pieces,
        selected_pos=selected_pos,
        legal_move_targets=tuple(parse_square(sq) for sq in state["legal_move_targets"]),
        castle_targets=tuple(parse_square(sq) for sq in state["castle_targets"]),
        active_movements=tuple(_decode_movement(m) for m in state["active_movements"]),
        cooldown_positions=tuple(parse_square(sq) for sq in state["cooldown_positions"]),
        clock_ms=state["clock_ms"],
        game_over=state["game_over"],
        game_over_reason=state["game_over_reason"],
        winner=state["winner"],
    )


def _decode_piece(piece_data: Dict[str, Any]) -> PieceSnapshot:
    return PieceSnapshot(
        color=piece_data["color"],
        piece_type=piece_data["piece_type"],
        has_moved=piece_data["has_moved"],
        can_select=piece_data["can_select"],
        can_move=piece_data["can_move"],
        state=PieceVisualState[piece_data["state"]],
        state_elapsed_millis=piece_data["state_elapsed_ms"],
        state_duration_millis=piece_data["state_duration_ms"],
    )


def _decode_movement(movement_data: Dict[str, Any]) -> MovementSnapshot:
    placeholder_piece = PieceSnapshot(
        color=movement_data["color"],
        piece_type=movement_data["piece_type"],
        has_moved=False,
        can_select=False,
        can_move=False,
        state=PieceVisualState.IDLE,
        state_elapsed_millis=_UNTRANSMITTED_STATE_MS,
        state_duration_millis=_UNTRANSMITTED_STATE_MS,
    )
    return MovementSnapshot(
        frm=parse_square(movement_data["from"]),
        to=parse_square(movement_data["to"]),
        piece=placeholder_piece,
        start_ms=movement_data["start_ms"],
        arrival_ms=movement_data["arrival_ms"],
    )
