"""Wire <-> domain mapping: algebraic coordinates and snapshot serialization.

Layer: application (server/application/dtos)
Owns: algebraic notation conversion (e.g. "e2" <-> Position(6, 4)), GameSnapshot
serialization for clients, and inbound frame parsing/validation.
Must not own: game logic, network I/O, or state management.
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from core.config import consts
from core.model.position import Position
from core.view.game_snapshot import GameSnapshot

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
    """Serializes GameSnapshot DTO into a JSON-friendly dict for network transport.

    Split in two on purpose. The board — pieces, movements, cooldowns, clock —
    is identical for everyone in the room and is serialized once per broadcast.
    The selection-derived fields (`selected_pos`, `legal_move_targets`,
    `castle_targets`) belong to exactly one player: they are what *that* player
    currently has picked up and where it may go. Sending them to the opponent
    leaks intent, and sending them to a spectator is meaningless.
    """

    @staticmethod
    def serialize(snapshot: GameSnapshot) -> Dict[str, Any]:
        """The complete snapshot, selection included.

        Used where the recipient is known to own the whole view — the reconnect
        resync, and any caller that has no per-recipient split to make.
        """
        payload = SnapshotSerializer.serialize_shared(snapshot)
        payload.update(SnapshotSerializer.selection_fields(snapshot))
        return payload

    @staticmethod
    def serialize_for(snapshot: GameSnapshot, viewer_color: Optional[str]) -> Dict[str, Any]:
        """The snapshot as *viewer_color* is allowed to see it."""
        payload = SnapshotSerializer.serialize_shared(snapshot)
        payload.update(SnapshotSerializer.selection_fields(snapshot, viewer_color))
        return payload

    @staticmethod
    def selection_fields(
        snapshot: GameSnapshot, viewer_color: Optional[str] = None
    ) -> Dict[str, Any]:
        """The selection-derived fields, blanked unless they belong to *viewer_color*.

        Passing no color returns them unconditionally, which is the reconnect
        case: that recipient is the player whose selection it is, or the caller
        has already decided it may see everything.

        Ownership is read from the colour of the piece standing on the selected
        square — the engine holds one selection for the whole game, so the piece
        is the only thing that says whose it is.
        """
        owner = SnapshotSerializer._selection_owner(snapshot)
        withheld = viewer_color is not None and owner != viewer_color
        if snapshot.selected_pos is None or withheld:
            return {
                ff.FIELD_SELECTED_POS: None,
                ff.FIELD_LEGAL_MOVE_TARGETS: [],
                ff.FIELD_CASTLE_TARGETS: [],
            }
        return {
            ff.FIELD_SELECTED_POS: AlgebraicParser.format_square(snapshot.selected_pos),
            ff.FIELD_LEGAL_MOVE_TARGETS: [
                AlgebraicParser.format_square(p) for p in snapshot.legal_move_targets
            ],
            ff.FIELD_CASTLE_TARGETS: [
                AlgebraicParser.format_square(p) for p in snapshot.castle_targets
            ],
        }

    @staticmethod
    def _selection_owner(snapshot: GameSnapshot) -> Optional[str]:
        if snapshot.selected_pos is None:
            return None
        selected = snapshot.pieces.get(snapshot.selected_pos)
        return selected.color if selected is not None else None

    @staticmethod
    def serialize_shared(snapshot: GameSnapshot) -> Dict[str, Any]:
        """Everything in the snapshot that is the same for every recipient."""
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

        return {
            ff.FIELD_ROWS: snapshot.rows,
            ff.FIELD_COLS: snapshot.cols,
            ff.FIELD_PIECES: pieces_dict,
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
