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

from server.application.dtos import frame_fields as ff

# A square identifier is exactly file letter + rank digit, e.g. "e2".
_SQUARE_TOKEN_LENGTH = 2
# Attribute probed to render enum-like piece states by name.
_ATTR_NAME = "name"


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
        if not isinstance(square, str) or len(square) != _SQUARE_TOKEN_LENGTH:
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
                ff.FIELD_COLOR: piece_snap.color,
                ff.FIELD_PIECE_TYPE: piece_snap.piece_type,
                ff.FIELD_HAS_MOVED: piece_snap.has_moved,
                ff.FIELD_CAN_SELECT: piece_snap.can_select,
                ff.FIELD_CAN_MOVE: piece_snap.can_move,
                ff.FIELD_STATE: piece_snap.state.name if hasattr(piece_snap.state, _ATTR_NAME) else str(piece_snap.state),
                ff.FIELD_STATE_ELAPSED_MS: piece_snap.state_elapsed_millis,
                ff.FIELD_STATE_DURATION_MS: piece_snap.state_duration_millis,
            }

        movements_list: List[Dict[str, Any]] = []
        for m in snapshot.active_movements:
            movements_list.append({
                ff.FIELD_FROM: AlgebraicParser.format_square(m.frm),
                ff.FIELD_TO: AlgebraicParser.format_square(m.to),
                ff.FIELD_COLOR: m.piece.color,
                ff.FIELD_PIECE_TYPE: m.piece.piece_type,
                ff.FIELD_START_MS: m.start_ms,
                ff.FIELD_ARRIVAL_MS: m.arrival_ms,
            })

        cooldowns_list = [AlgebraicParser.format_square(p) for p in snapshot.cooldown_positions]
        legal_targets = [AlgebraicParser.format_square(p) for p in snapshot.legal_move_targets]
        castle_targets = [AlgebraicParser.format_square(p) for p in snapshot.castle_targets]

        selected = AlgebraicParser.format_square(snapshot.selected_pos) if snapshot.selected_pos else None

        return {
            ff.FIELD_ROWS: snapshot.rows,
            ff.FIELD_COLS: snapshot.cols,
            ff.FIELD_PIECES: pieces_dict,
            ff.FIELD_SELECTED_POS: selected,
            ff.FIELD_LEGAL_MOVE_TARGETS: legal_targets,
            ff.FIELD_CASTLE_TARGETS: castle_targets,
            ff.FIELD_ACTIVE_MOVEMENTS: movements_list,
            ff.FIELD_COOLDOWN_POSITIONS: cooldowns_list,
            ff.FIELD_CLOCK_MS: snapshot.clock_ms,
            ff.FIELD_GAME_OVER: snapshot.game_over,
            ff.FIELD_GAME_OVER_REASON: snapshot.game_over_reason,
            ff.FIELD_WINNER: snapshot.winner,
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

    if not isinstance(data, dict) or ff.FIELD_TYPE not in data:
        raise ValueError("Payload must be a JSON object containing a 'type' field")

    return data
