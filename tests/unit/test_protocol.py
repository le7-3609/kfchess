"""Unit tests for protocol DTOs and AlgebraicParser."""

import json
import pytest
from shared.model.position import Position
from server.application.dtos.network_frames import MSG_ERROR, MSG_GAME_START, MSG_MOVE
from server.application.dtos.protocol_mapper import (
    AlgebraicParser,
    SnapshotSerializer,
    parse_client_message,
)
from server.application.dtos.response_frames import (
    build_error_message,
    build_game_start_message,
    build_move_message,
)
from shared.view.game_snapshot import GameSnapshot, PieceSnapshot
from shared.view.piece_visual_state import PieceVisualState


def test_parse_square_a1():
    pos = AlgebraicParser.parse_square("a1")
    assert pos == Position(row=7, col=0)


def test_parse_square_e2():
    pos = AlgebraicParser.parse_square("e2")
    assert pos == Position(row=6, col=4)


def test_parse_square_h8():
    pos = AlgebraicParser.parse_square("h8")
    assert pos == Position(row=0, col=7)


def test_parse_square_a8():
    pos = AlgebraicParser.parse_square("a8")
    assert pos == Position(row=0, col=0)


def test_format_square_roundtrip():
    for file_char in "abcdefgh":
        for rank in range(1, 9):
            sq = f"{file_char}{rank}"
            pos = AlgebraicParser.parse_square(sq)
            formatted = AlgebraicParser.format_square(pos)
            assert formatted == sq


def test_parse_square_invalid():
    with pytest.raises(ValueError):
        AlgebraicParser.parse_square("z1")
    with pytest.raises(ValueError):
        AlgebraicParser.parse_square("a0")
    with pytest.raises(ValueError):
        AlgebraicParser.parse_square("a9")
    with pytest.raises(ValueError):
        AlgebraicParser.parse_square("")
    with pytest.raises(ValueError):
        AlgebraicParser.parse_square("a12")


def test_parse_move():
    src, dst = AlgebraicParser.parse_move("e2", "e4")
    assert src == Position(6, 4)
    assert dst == Position(4, 4)


def test_build_messages():
    move_msg = build_move_message("e2", "e4")
    assert move_msg == {"type": MSG_MOVE, "from": "e2", "to": "e4"}

    err_msg = build_error_message("Invalid move")
    assert err_msg == {"type": MSG_ERROR, "message": "Invalid move"}

    start_msg = build_game_start_message("w", "Player_2")
    assert start_msg == {"type": MSG_GAME_START, "color": "w", "opponent": "Player_2"}


def test_parse_client_message_valid():
    raw = json.dumps({"type": "move", "from": "e2", "to": "e4"})
    parsed = parse_client_message(raw)
    assert parsed["type"] == "move"
    assert parsed["from"] == "e2"


def test_parse_client_message_invalid():
    with pytest.raises(ValueError):
        parse_client_message("not json")

    with pytest.raises(ValueError):
        parse_client_message(json.dumps({"no_type": "value"}))


def test_snapshot_serializer():
    p_snap = PieceSnapshot(
        color="w",
        piece_type="P",
        has_moved=False,
        can_select=True,
        can_move=True,
        state=PieceVisualState.IDLE,
        state_elapsed_millis=0,
        state_duration_millis=0,
    )
    snap = GameSnapshot(
        rows=8,
        cols=8,
        pieces={Position(6, 4): p_snap},
        selected_pos=Position(6, 4),
        legal_move_targets=(Position(4, 4),),
        castle_targets=(),
        active_movements=(),
        cooldown_positions=(),
        clock_ms=100,
        game_over=False,
        game_over_reason=None,
        winner=None,
    )

    serialized = SnapshotSerializer.serialize(snap)
    assert serialized["rows"] == 8
    assert serialized["cols"] == 8
    assert "e2" in serialized["pieces"]
    assert serialized["pieces"]["e2"]["color"] == "w"
    assert serialized["selected_pos"] == "e2"
    assert serialized["legal_move_targets"] == ["e4"]
