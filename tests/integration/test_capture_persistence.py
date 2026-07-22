"""End-to-end capture persistence: real engine events -> logs -> database rows.

Drives a real RealTimeArbiter (timed movement) with MovesLog and CaptureLog
subscribed to the same bus — exactly how the server's GameRoom wires them —
then decomposes the logs through persisted_moves_from_log and round-trips the
result through the SQLite adapter. Pins the cross-module contract the unit
tests each assume from their own side: the capture events the engine actually
publishes carry enough to attribute every completed capturing move, for
arrival captures and mid-transit collision captures alike.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from server.application.capture_log import CaptureLog
from server.application.game_result import GameResult, persisted_moves_from_log
from server.infrastructure.database.database import Database
from shared.config.game_config import GameConfig
from shared.events import EventBus, PieceCapturedEvent, PieceMovedEvent
from shared.io.moves_log import MovesLog
from shared.model.board import ArrayBoard
from shared.model.game_state import GameState, Movement
from shared.model.piece import TextPiece as Piece
from shared.model.position import Position
from shared.realtime.real_time_arbiter import ChebyshevDistanceDuration, RealTimeArbiter
from shared.rules.piece_rules import (
    BishopMoveValidator,
    KingMoveValidator,
    KnightMoveValidator,
    MoveValidatorFactory,
    PawnMoveValidator,
    QueenMoveValidator,
    RookMoveValidator,
    StandardPawnPromotion,
)
from shared.rules.rule_engine import PathChecker

MS_PER_SQUARE = 1000


class _LiveGame:
    """A real arbiter with the server's log pair on its bus."""

    def __init__(self) -> None:
        config = GameConfig()
        factory = MoveValidatorFactory({
            "K": KingMoveValidator(),
            "Q": QueenMoveValidator(),
            "R": RookMoveValidator(),
            "B": BishopMoveValidator(),
            "N": KnightMoveValidator(),
            "P": PawnMoveValidator(config),
        })
        event_bus = EventBus()
        self.moves_log = MovesLog()
        self.capture_log = CaptureLog()
        event_bus.subscribe(self.moves_log, PieceMovedEvent)
        event_bus.subscribe(self.capture_log, PieceCapturedEvent)
        self.arbiter = RealTimeArbiter(
            duration_strategy=ChebyshevDistanceDuration(ms_per_square=MS_PER_SQUARE),
            path_checker=PathChecker(factory, config),
            config=config,
            promotion_strategy=StandardPawnPromotion(),
            event_bus=event_bus,
        )
        self.board = ArrayBoard(8, 8)
        self.state = GameState()

    def start_move(self, piece: Piece, frm: Position, to: Position, start_ms: int, arrival_ms: int) -> None:
        self.board.set_piece(frm, piece)
        self.arbiter.register_motion(
            Movement(frm=frm, to=to, piece=piece, start_ms=start_ms, arrival_ms=arrival_ms)
        )

    def run_until(self, t: int) -> None:
        self.state.clock_ms = t
        self.arbiter.resolve_movements(self.board, self.state, t)

    def persisted_rows(self):
        return persisted_moves_from_log(self.moves_log.entries(), self.capture_log.records())


def test_arrival_capture_reaches_persisted_move():
    game = _LiveGame()
    # Black pawn stands on a4; the white rook travels a1 -> a4 and takes it.
    game.board.set_piece(Position(4, 0), Piece("b", "P"))
    game.start_move(Piece("w", "R"), Position(7, 0), Position(4, 0), start_ms=0, arrival_ms=3000)

    game.run_until(3000)

    rows = game.persisted_rows()
    assert [(r.from_square, r.to_square) for r in rows] == [("a1", "a4")]
    assert rows[0].captured_piece == "P"


def test_mid_transit_collision_capture_reaches_persisted_move():
    game = _LiveGame()
    # Two enemy rooks swap a1 <-> a2. The later mover wins the collision
    # mid-transit — before it arrives, away from the collision instant — then
    # completes its move, which must still end up annotated with the kill.
    game.start_move(Piece("w", "R"), Position(7, 0), Position(6, 0), start_ms=0, arrival_ms=1000)
    game.start_move(Piece("b", "R"), Position(6, 0), Position(7, 0), start_ms=100, arrival_ms=1100)

    game.run_until(1100)

    rows = game.persisted_rows()
    # The white rook died in flight and never produced a move row.
    assert [(r.from_square, r.to_square, r.piece_color) for r in rows] == [("a2", "a1", "black")]
    assert rows[0].captured_piece == "R"
    # The capture really was a collision one: it predates the captor's arrival.
    capture = game.capture_log.records()[0]
    assert capture.at_ms < 1100
    assert (capture.captor_from, capture.captor_to) == ("a2", "a1")


@pytest_asyncio.fixture
async def temp_db(tmp_path):
    db = Database(str(tmp_path / "test.db"))
    await db.connect()
    yield db
    await db.close()


@pytest.mark.asyncio
async def test_engine_captures_round_trip_through_database(temp_db):
    game = _LiveGame()
    game.board.set_piece(Position(4, 0), Piece("b", "P"))
    game.start_move(Piece("w", "R"), Position(7, 0), Position(4, 0), start_ms=0, arrival_ms=3000)
    game.run_until(3000)

    white_id = await temp_db.create_user("white", "pw")
    black_id = await temp_db.create_user("black", "pw")
    rows = game.persisted_rows()
    result = GameResult(
        room_id="CAPTURE_IT",
        white_player_id=white_id,
        black_player_id=black_id,
        winner_id=white_id,
        result="checkmate",
        white_elo_before=1200,
        white_elo_after=1216,
        black_elo_before=1200,
        black_elo_after=1184,
        started_at=datetime(2026, 7, 22, tzinfo=timezone.utc),
        ended_at=datetime(2026, 7, 22, 0, 5, tzinfo=timezone.utc),
        moves=rows,
    )

    game_id = await temp_db.save_completed_game(result, result.moves)

    saved = await temp_db.get_moves(game_id)
    assert len(saved) == 1
    # (move_number, from, to, piece_type, color, captured_piece, timestamp)
    assert saved[0][1:3] == ("a1", "a4")
    assert saved[0][5] == "P"
