"""Wire <-> domain mapping: algebraic coordinates and snapshot serialization.

Layer: application (server/application/dtos)
Owns: algebraic notation conversion (e.g. "e2" <-> Position(6, 4)), GameSnapshot
serialization for clients, and inbound frame parsing/validation.
Must not own: game logic, network I/O, or state management.
"""

import json
from typing import Any, Dict, List, Tuple

from shared.config import consts
from shared.model.position import Position
from shared.view.game_snapshot import GameSnapshot


class AlgebraicParser:
    """Translates algebraic square notation ("e2", "a1") directly to Position structs.

    Optimization A: This maps algebraic notation straight to Position(row, col)
    without pixel-coordinate conversions.
    """

    @staticmethod
    def parse_square(square: str) -> Position:
        """Parse an algebraic square identifier like 'e2' into a Position.

        Raises:
            ValueError: If square is malformed or out of board bounds.
        """
        if not isinstance(square, str) or len(square) != 2:
            raise ValueError(f"Invalid square notation: {square!r}")

        file_char = square[0].lower()
        rank_char = square[1]

        if file_char not in consts.NOTATION_FILES:
            raise ValueError(f"Invalid file letter in square {square!r}")

        try:
            rank_num = int(rank_char)
        except ValueError as exc:
            raise ValueError(f"Invalid rank number in square {square!r}") from exc

        if not (1 <= rank_num <= consts.NOTATION_RANKS):
            raise ValueError(f"Rank number out of bounds in square {square!r}")

        col = consts.NOTATION_FILES.index(file_char)
        row = consts.NOTATION_RANKS - rank_num
        return Position(row=row, col=col)

    @staticmethod
    def format_square(pos: Position) -> str:
        """Format a Position(row, col) into algebraic square notation like 'e2'."""
        if not (0 <= pos.col < len(consts.NOTATION_FILES)):
            raise ValueError(f"Position col out of bounds: {pos.col}")
        if not (0 <= pos.row < consts.NOTATION_RANKS):
            raise ValueError(f"Position row out of bounds: {pos.row}")

        file_char = consts.NOTATION_FILES[pos.col]
        rank_num = consts.NOTATION_RANKS - pos.row
        return f"{file_char}{rank_num}"

    @staticmethod
    def parse_move(from_sq: str, to_sq: str) -> Tuple[Position, Position]:
        """Parse a pair of square strings into (source_pos, dest_pos)."""
        src = AlgebraicParser.parse_square(from_sq)
        dst = AlgebraicParser.parse_square(to_sq)
        return src, dst


class SnapshotSerializer:
    """Serializes GameSnapshot DTO into a JSON-friendly dict for network transport."""

    @staticmethod
    def serialize(snapshot: GameSnapshot) -> Dict[str, Any]:
        pieces_dict: Dict[str, Dict[str, Any]] = {}
        for pos, piece_snap in snapshot.pieces.items():
            sq_str = AlgebraicParser.format_square(pos)
            pieces_dict[sq_str] = {
                "color": piece_snap.color,
                "piece_type": piece_snap.piece_type,
                "has_moved": piece_snap.has_moved,
                "can_select": piece_snap.can_select,
                "can_move": piece_snap.can_move,
                "state": piece_snap.state.name if hasattr(piece_snap.state, "name") else str(piece_snap.state),
                "state_elapsed_ms": piece_snap.state_elapsed_millis,
                "state_duration_ms": piece_snap.state_duration_millis,
            }

        movements_list: List[Dict[str, Any]] = []
        for m in snapshot.active_movements:
            movements_list.append({
                "from": AlgebraicParser.format_square(m.frm),
                "to": AlgebraicParser.format_square(m.to),
                "color": m.piece.color,
                "piece_type": m.piece.piece_type,
                "start_ms": m.start_ms,
                "arrival_ms": m.arrival_ms,
            })

        cooldowns_list = [AlgebraicParser.format_square(p) for p in snapshot.cooldown_positions]
        legal_targets = [AlgebraicParser.format_square(p) for p in snapshot.legal_move_targets]
        castle_targets = [AlgebraicParser.format_square(p) for p in snapshot.castle_targets]

        selected = AlgebraicParser.format_square(snapshot.selected_pos) if snapshot.selected_pos else None

        return {
            "rows": snapshot.rows,
            "cols": snapshot.cols,
            "pieces": pieces_dict,
            "selected_pos": selected,
            "legal_move_targets": legal_targets,
            "castle_targets": castle_targets,
            "active_movements": movements_list,
            "cooldown_positions": cooldowns_list,
            "clock_ms": snapshot.clock_ms,
            "game_over": snapshot.game_over,
            "game_over_reason": snapshot.game_over_reason,
            "winner": snapshot.winner,
        }


def parse_client_message(raw_json: str) -> Dict[str, Any]:
    """Parse raw JSON string from WebSocket and ensure 'type' field is present.

    Raises:
        ValueError: If JSON is invalid or 'type' field is missing.
    """
    try:
        data = json.loads(raw_json)
    except Exception as exc:
        raise ValueError(f"Invalid JSON payload: {exc}") from exc

    if not isinstance(data, dict) or "type" not in data:
        raise ValueError("Payload must be a JSON object containing a 'type' field")

    return data
