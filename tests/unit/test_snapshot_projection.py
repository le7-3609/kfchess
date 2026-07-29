"""Unit tests for the client's board projection.

The server no longer broadcasts a snapshot every tick, so the client draws from
a projection it maintains itself between reconciliation frames. These tests pin
the two things that projection has to get right: a piece stays visible and
correctly placed through its whole journey, and an authoritative snapshot always
wins over whatever was projected.
"""

import pytest

from client.network.snapshot_projection import COOLDOWN_DURATION_MS, SnapshotProjector
from core.model.position import Position
from core.view.game_snapshot import GameSnapshot, PieceSnapshot
from core.view.piece_visual_state import PieceVisualState

E2 = Position(row=6, col=4)
E4 = Position(row=4, col=4)
D7 = Position(row=1, col=3)
E8 = Position(row=0, col=4)


class _Clock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance_ms(self, milliseconds: float) -> None:
        self.now += milliseconds / 1000.0


def _piece(color="w", piece_type="P", state=PieceVisualState.IDLE, elapsed=0, duration=0):
    return PieceSnapshot(
        color=color,
        piece_type=piece_type,
        has_moved=False,
        can_select=True,
        can_move=True,
        state=state,
        state_elapsed_millis=elapsed,
        state_duration_millis=duration,
    )


def _snapshot(pieces=None, clock_ms=0, **kwargs):
    defaults = dict(
        rows=8,
        cols=8,
        pieces=pieces if pieces is not None else {E2: _piece(), D7: _piece(color="b")},
        selected_pos=None,
        legal_move_targets=(),
        castle_targets=(),
        active_movements=(),
        cooldown_positions=(),
        clock_ms=clock_ms,
        game_over=False,
        game_over_reason=None,
        winner=None,
    )
    defaults.update(kwargs)
    return GameSnapshot(**defaults)


def _projector(clock=None):
    return SnapshotProjector(time_fn=clock or _Clock()), clock


def test_nothing_is_projected_before_the_first_snapshot():
    """Events that arrive before any board must not invent one."""
    projector = SnapshotProjector(time_fn=_Clock())

    projector.apply_event(
        {"type": "event_move_started", "from": "e2", "to": "e4", "at_ms": 0,
         "arrival_ms": 500, "color": "w", "piece_type": "P"}
    )

    assert not projector.has_state
    assert projector.project() is None


def test_a_snapshot_is_projected_back_unchanged_at_the_same_instant():
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)

    projector.apply_snapshot(_snapshot(clock_ms=1234))
    projected = projector.project()

    assert projected.clock_ms == 1234
    assert set(projected.pieces) == {E2, D7}


def test_the_clock_advances_locally_between_snapshots():
    """This is what lets the renderer interpolate travel without the server
    sending a frame per tick."""
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))

    clock.advance_ms(250)

    assert projector.project().clock_ms == pytest.approx(1250, abs=2)


def test_a_started_move_puts_the_piece_in_flight_from_its_origin():
    """An in-flight piece is keyed by the square it left, matching how the
    server's own snapshot builder reports it — the renderer interpolates its
    drawn position from the movement."""
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))

    projector.apply_event(
        {"type": "event_move_started", "from": "e2", "to": "e4", "at_ms": 1000,
         "arrival_ms": 1600, "color": "w", "piece_type": "P"}
    )
    projected = projector.project()

    assert len(projected.active_movements) == 1
    movement = projected.active_movements[0]
    assert (movement.frm, movement.to) == (E2, E4)
    assert movement.start_ms == 1000 and movement.arrival_ms == 1600
    assert projected.pieces[E2].state == PieceVisualState.MOVE
    assert E4 not in projected.pieces


def test_a_travelling_pieces_elapsed_time_grows_with_the_local_clock():
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))
    projector.apply_event(
        {"type": "event_move_started", "from": "e2", "to": "e4", "at_ms": 1000,
         "arrival_ms": 1600, "color": "w", "piece_type": "P"}
    )

    clock.advance_ms(300)
    piece = projector.project().pieces[E2]

    assert piece.state_duration_millis == 600
    assert piece.state_elapsed_millis == pytest.approx(300, abs=20)


def test_a_completed_move_lands_the_piece_and_starts_its_cooldown():
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))
    projector.apply_event(
        {"type": "event_move_started", "from": "e2", "to": "e4", "at_ms": 1000,
         "arrival_ms": 1600, "color": "w", "piece_type": "P"}
    )

    projector.apply_event(
        {"type": "event_piece_moved", "from": "e2", "to": "e4", "at_ms": 1600,
         "color": "w", "piece_type": "P", "was_capture": False}
    )
    projected = projector.project()

    assert E2 not in projected.pieces
    assert projected.pieces[E4].state == PieceVisualState.SHORT_REST
    assert E4 in projected.cooldown_positions
    assert projected.active_movements == ()


def test_a_cooldown_expires_locally_without_being_told():
    """The cooldown duration is a fixed engine constant, so the client can
    reproduce it exactly rather than waiting for a snapshot to say so."""
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))
    projector.apply_event(
        {"type": "event_move_started", "from": "e2", "to": "e4", "at_ms": 1000,
         "arrival_ms": 1600, "color": "w", "piece_type": "P"}
    )
    projector.apply_event(
        {"type": "event_piece_moved", "from": "e2", "to": "e4", "at_ms": 1600,
         "color": "w", "piece_type": "P", "was_capture": False}
    )

    clock.advance_ms(COOLDOWN_DURATION_MS + 50)
    piece = projector.project().pieces[E4]

    assert piece.state == PieceVisualState.IDLE
    assert piece.can_move


def test_an_arrival_is_predicted_when_its_event_has_not_arrived_yet():
    """Without this a piece would freeze on its origin square whenever the
    confirming event is late, which is exactly when the animation matters."""
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))
    projector.apply_event(
        {"type": "event_move_started", "from": "e2", "to": "e4", "at_ms": 1000,
         "arrival_ms": 1600, "color": "w", "piece_type": "P"}
    )

    clock.advance_ms(700)  # past arrival, with no event_piece_moved
    projected = projector.project()

    assert E4 in projected.pieces
    assert E2 not in projected.pieces


def test_a_capture_removes_the_taken_piece():
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))

    projector.apply_event(
        {"type": "event_piece_captured", "pos": "d7", "at_ms": 1100, "color": "b",
         "piece_type": "P", "captor_color": "w", "captor_piece_type": "P",
         "captor_from": "e2", "captor_to": "d7"}
    )

    assert D7 not in projector.project().pieces


def test_a_capture_also_clears_a_victim_that_was_still_in_flight():
    """A collision capture strikes mid-transit, at neither endpoint of the
    victim's own movement, so removing only `pos` would leave a ghost."""
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))
    projector.apply_event(
        {"type": "event_move_started", "from": "d7", "to": "d5", "at_ms": 1000,
         "arrival_ms": 2000, "color": "b", "piece_type": "P"}
    )

    projector.apply_event(
        {"type": "event_piece_captured", "pos": "d5", "at_ms": 1500, "color": "b",
         "piece_type": "P", "captor_color": "w", "captor_piece_type": "N",
         "captor_from": "b1", "captor_to": "d5"}
    )
    projected = projector.project()

    assert projected.active_movements == ()
    assert D7 not in projected.pieces


def test_an_aborted_move_leaves_the_piece_where_it_stopped():
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))
    projector.apply_event(
        {"type": "event_move_started", "from": "e2", "to": "e4", "at_ms": 1000,
         "arrival_ms": 1600, "color": "w", "piece_type": "P"}
    )

    projector.apply_event(
        {"type": "event_move_aborted", "from": "e2", "stopped_at": "e2", "at_ms": 1200,
         "color": "w", "piece_type": "P", "reason": "path_blocked"}
    )
    projected = projector.project()

    assert projected.active_movements == ()
    assert E2 in projected.pieces


def test_a_promotion_changes_the_piece_type_in_place():
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(pieces={E8: _piece(piece_type="P")}, clock_ms=1000))

    projector.apply_event(
        {"type": "event_piece_promoted", "pos": "e8", "at_ms": 1100, "color": "w",
         "from_piece_type": "P", "to_piece_type": "Q"}
    )

    assert projector.project().pieces[E8].piece_type == "Q"


def test_game_over_freezes_the_projected_clock():
    """The game clock only advances while the game runs; extrapolating past the
    end would keep animating cooldowns on a finished board."""
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=5000))
    projector.apply_event(
        {"type": "event_game_ended", "reason": "checkmate", "winner": "w", "at_ms": 5000}
    )

    clock.advance_ms(2000)
    projected = projector.project()

    assert projected.game_over
    assert projected.winner == "w"
    assert projected.clock_ms == 5000


def test_a_reconciliation_snapshot_overrides_a_mis_projection():
    """The projection is never authoritative: whatever it got wrong is repaired
    within one reconciliation interval."""
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=1000))
    projector.apply_event(
        {"type": "event_move_started", "from": "e2", "to": "e4", "at_ms": 1000,
         "arrival_ms": 1600, "color": "w", "piece_type": "P"}
    )

    projector.apply_snapshot(_snapshot(pieces={D7: _piece(color="b")}, clock_ms=4000))
    projected = projector.project()

    assert set(projected.pieces) == {D7}
    assert projected.active_movements == ()
    assert projected.clock_ms == 4000


def test_an_out_of_order_event_never_drags_the_clock_backward():
    """The simulation clock never flows backward, and neither may its
    projection."""
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    projector.apply_snapshot(_snapshot(clock_ms=5000))

    projector.apply_event(
        {"type": "event_piece_captured", "pos": "d7", "at_ms": 10, "color": "b",
         "piece_type": "P", "captor_color": "w", "captor_piece_type": "P",
         "captor_from": "e2", "captor_to": "d7"}
    )

    assert projector.project().clock_ms >= 5000


def test_a_snapshot_recovers_cooldowns_already_in_progress():
    """A reconnecting client must not see a half-elapsed cooldown restart."""
    clock = _Clock()
    projector = SnapshotProjector(time_fn=clock)
    resting = _piece(state=PieceVisualState.SHORT_REST, elapsed=400, duration=1000)

    projector.apply_snapshot(_snapshot(pieces={E4: resting}, clock_ms=2000))
    clock.advance_ms(500)
    piece = projector.project().pieces[E4]

    assert piece.state == PieceVisualState.SHORT_REST
    assert piece.state_elapsed_millis == pytest.approx(900, abs=20)


def test_unknown_frames_are_ignored():
    projector = SnapshotProjector(time_fn=_Clock())
    projector.apply_snapshot(_snapshot(clock_ms=1000))

    projector.apply_event({"type": "info", "message": "hello"})

    assert set(projector.project().pieces) == {E2, D7}
    assert not projector.handles("info")
