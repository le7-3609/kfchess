"""Unit tests for CaptureLog event recording and algebraic conversion."""

from server.application.capture_log import CaptureLog, CaptureRecord
from shared.events import GameStartedEvent, PieceCapturedEvent
from shared.model.position import Position


def _captured(at_ms, pos, piece_type="P", color="b", captor_color="w",
              captor_frm=Position(7, 3), captor_to=Position(3, 7)):
    return PieceCapturedEvent(
        at_ms=at_ms,
        color=color,
        piece_type=piece_type,
        pos=pos,
        captor_color=captor_color,
        captor_piece_type="Q",
        captor_frm=captor_frm,
        captor_to=captor_to,
    )


def test_records_capture_with_algebraic_squares():
    log = CaptureLog()
    # row=0, col=0 is a8; row=6, col=4 is e2; row=7, col=3 is d1; row=3, col=7 is h5.
    log.on_event(_captured(1200, Position(6, 4)))

    records = log.records()
    assert records == [
        CaptureRecord(
            at_ms=1200, square="e2", piece_type="P", color="b",
            captor_from="d1", captor_to="h5",
        )
    ]


def test_ignores_non_capture_events():
    log = CaptureLog()
    log.on_event(GameStartedEvent(at_ms=0, rows=8, cols=8))
    assert log.records() == []


def test_preserves_order_and_multiple_captures():
    log = CaptureLog()
    log.on_event(_captured(500, Position(0, 0), piece_type="R"))
    log.on_event(_captured(900, Position(7, 7), piece_type="N"))

    squares = [(r.square, r.piece_type) for r in log.records()]
    assert squares == [("a8", "R"), ("h1", "N")]


def test_records_returns_a_copy():
    log = CaptureLog()
    log.on_event(_captured(100, Position(1, 1)))
    snapshot = log.records()
    log.on_event(_captured(200, Position(2, 2)))
    assert len(snapshot) == 1  # earlier snapshot is not mutated
