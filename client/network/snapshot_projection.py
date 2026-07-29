"""Client-side board projection between server snapshots (client layer).

Owns: the client's working copy of the board — advancing its clock from local
wall time, applying `event_*` frames to it, and producing the GameSnapshot the
renderer draws each frame.
Must not own: network I/O, GUI state, or game rules. Nothing here decides
whether a move is legal; it reflects moves the server has already ruled on.

Why this exists: the server used to broadcast a complete ~5.5 KB snapshot 20
times a second, and the client simply drew whatever arrived. That is 440x more
traffic than the game needs, so the server now sends sparse events plus a full
snapshot every few seconds. Pieces travel over time rather than teleporting, so
an event carrying `from`, `to` and `arrival_ms` is enough to draw the whole
journey locally — this class is what turns that stream back into a board.

Its state is deliberately *not* authoritative. Every field is overwritten by the
next reconciliation snapshot, so a mis-projection costs at most a few seconds of
cosmetic drift and can never disagree with the server about the actual game.
"""

import time
from typing import Any, Dict, List, Optional

from client.network import protocol
from client.notation.algebraic_notation import parse_square
from core.config import consts
from core.model.position import Position
from core.view.game_snapshot import GameSnapshot, MovementSnapshot, PieceSnapshot
from core.view.piece_visual_state import PieceVisualState

# The engine's cooldown is a fixed configuration constant, so the client can
# reproduce it exactly rather than waiting to be told. Imported from `core`,
# which the client already depends on, so the two cannot drift apart.
COOLDOWN_DURATION_MS = consts.DEFAULT_COOLDOWN_DURATION_MS

_NO_DURATION = 0


class SnapshotProjector:
    """Maintains a projected GameSnapshot from full snapshots plus events."""

    def __init__(
        self,
        cooldown_duration_ms: int = COOLDOWN_DURATION_MS,
        time_fn=time.monotonic,
    ) -> None:
        self._cooldown_duration_ms = cooldown_duration_ms
        self._time_fn = time_fn

        self._rows = 0
        self._cols = 0
        self._pieces: Dict[Position, PieceSnapshot] = {}
        # Keyed by origin square, matching how the snapshot keys an in-flight
        # piece: the board still holds it at `frm` until it lands.
        self._movements: Dict[Position, MovementSnapshot] = {}
        # Square -> game-clock instant its occupant's cooldown ends.
        self._cooldown_until: Dict[Position, int] = {}

        self._selected_pos: Optional[Position] = None
        self._legal_move_targets: tuple = ()
        self._castle_targets: tuple = ()

        self._anchor_clock_ms = 0
        self._anchor_wall_seconds = self._time_fn()

        self._game_over = False
        self._game_over_reason: Optional[str] = None
        self._winner: Optional[str] = None
        self._has_state = False

        self._event_handlers = {
            protocol.MSG_TYPE_EVENT_MOVE_STARTED: self._on_move_started,
            protocol.MSG_TYPE_EVENT_PIECE_MOVED: self._on_piece_moved,
            protocol.MSG_TYPE_EVENT_PIECE_CAPTURED: self._on_piece_captured,
            protocol.MSG_TYPE_EVENT_MOVE_ABORTED: self._on_move_aborted,
            protocol.MSG_TYPE_EVENT_PIECE_PROMOTED: self._on_piece_promoted,
            protocol.MSG_TYPE_EVENT_GAME_ENDED: self._on_game_ended,
        }

    @property
    def has_state(self) -> bool:
        """Whether a snapshot has ever arrived, i.e. there is a board to draw."""
        return self._has_state

    def handles(self, frame_type: str) -> bool:
        return frame_type in self._event_handlers

    # ------------------------------------------------------------------
    # Authoritative input
    # ------------------------------------------------------------------

    def apply_snapshot(self, snapshot: GameSnapshot) -> None:
        """Overwrite the projection with an authoritative server snapshot."""
        self._rows = snapshot.rows
        self._cols = snapshot.cols
        self._pieces = dict(snapshot.pieces)
        self._movements = {movement.frm: movement for movement in snapshot.active_movements}
        self._selected_pos = snapshot.selected_pos
        self._legal_move_targets = snapshot.legal_move_targets
        self._castle_targets = snapshot.castle_targets
        self._game_over = snapshot.game_over
        self._game_over_reason = snapshot.game_over_reason
        self._winner = snapshot.winner

        self._cooldown_until = self._cooldowns_from(snapshot)
        self._anchor(snapshot.clock_ms)
        self._has_state = True

    def _cooldowns_from(self, snapshot: GameSnapshot) -> Dict[Position, int]:
        """Recover each resting piece's cooldown end from its elapsed/duration pair."""
        return {
            pos: snapshot.clock_ms + max(
                0, piece.state_duration_millis - piece.state_elapsed_millis
            )
            for pos, piece in snapshot.pieces.items()
            if piece.state == PieceVisualState.SHORT_REST
        }

    # ------------------------------------------------------------------
    # Event input
    # ------------------------------------------------------------------

    def apply_event(self, frame: Dict[str, Any]) -> None:
        """Fold one `event_*` frame into the projection.

        Events that arrive before any snapshot are ignored: there is no board
        for them to modify, and the snapshot that follows carries their effect
        already.
        """
        handler = self._event_handlers.get(frame.get(protocol.FIELD_TYPE))
        if handler is None or not self._has_state:
            return
        self._advance_anchor_to(frame.get(protocol.FIELD_AT_MS))
        handler(frame)

    def _on_move_started(self, frame: Dict[str, Any]) -> None:
        origin = parse_square(frame[protocol.FIELD_FROM])
        destination = parse_square(frame[protocol.FIELD_TO])
        start_ms = int(frame.get(protocol.FIELD_AT_MS, self._anchor_clock_ms))
        arrival_ms = int(frame.get(protocol.FIELD_ARRIVAL_MS, start_ms))

        departing = self._pieces.get(origin)
        moving_piece = PieceSnapshot(
            color=frame[protocol.FIELD_COLOR],
            piece_type=frame[protocol.FIELD_PIECE_TYPE],
            has_moved=True,
            can_select=False,
            can_move=False,
            # A movement that ends where it began is the click-in-place jump;
            # every other move renders as travel, exactly as SnapshotBuilder
            # decides it server-side.
            state=PieceVisualState.JUMP if origin == destination else PieceVisualState.MOVE,
            state_elapsed_millis=0,
            state_duration_millis=max(0, arrival_ms - start_ms),
        )
        self._pieces[origin] = moving_piece
        self._cooldown_until.pop(origin, None)
        self._movements[origin] = MovementSnapshot(
            frm=origin,
            to=destination,
            piece=departing or moving_piece,
            start_ms=start_ms,
            arrival_ms=arrival_ms,
        )

    def _on_piece_moved(self, frame: Dict[str, Any]) -> None:
        origin = parse_square(frame[protocol.FIELD_FROM])
        destination = parse_square(frame[protocol.FIELD_TO])
        at_ms = int(frame.get(protocol.FIELD_AT_MS, self._anchor_clock_ms))
        self._land(origin, destination, at_ms, frame.get(protocol.FIELD_PIECE_TYPE))

    def _on_move_aborted(self, frame: Dict[str, Any]) -> None:
        """A move that failed mid-flight leaves its piece where it stopped."""
        origin = parse_square(frame[protocol.FIELD_FROM])
        stopped_at = parse_square(frame[protocol.FIELD_STOPPED_AT])
        at_ms = int(frame.get(protocol.FIELD_AT_MS, self._anchor_clock_ms))
        self._land(origin, stopped_at, at_ms, frame.get(protocol.FIELD_PIECE_TYPE))

    def _land(
        self, origin: Position, destination: Position, at_ms: int, piece_type: Optional[str]
    ) -> None:
        """Commit a travelling piece onto *destination* and start its cooldown."""
        movement = self._movements.pop(origin, None)
        travelling = self._pieces.pop(origin, None) or (
            movement.piece if movement is not None else None
        )
        if travelling is None:
            return

        self._pieces[destination] = PieceSnapshot(
            color=travelling.color,
            piece_type=piece_type or travelling.piece_type,
            has_moved=True,
            can_select=True,
            can_move=False,
            state=PieceVisualState.SHORT_REST,
            state_elapsed_millis=0,
            state_duration_millis=self._cooldown_duration_ms,
        )
        self._cooldown_until[destination] = at_ms + self._cooldown_duration_ms

    def _on_piece_captured(self, frame: Dict[str, Any]) -> None:
        """Remove a captured piece, wherever the capture caught it.

        A collision capture strikes mid-transit, at neither endpoint of the
        victim's own movement, so `pos` alone may not identify the square the
        projection has it keyed under — hence the movement sweep. Anything this
        misses is corrected by the next reconciliation snapshot.
        """
        square = parse_square(frame[protocol.FIELD_POS])
        self._remove_piece_at(square)
        for origin, movement in list(self._movements.items()):
            if square in (movement.frm, movement.to):
                self._movements.pop(origin, None)
                self._remove_piece_at(origin)

    def _remove_piece_at(self, square: Position) -> None:
        self._pieces.pop(square, None)
        self._cooldown_until.pop(square, None)

    def _on_piece_promoted(self, frame: Dict[str, Any]) -> None:
        square = parse_square(frame[protocol.FIELD_POS])
        promoted = self._pieces.get(square)
        if promoted is None:
            return
        self._pieces[square] = self._with_piece_type(
            promoted, frame[protocol.FIELD_TO_PIECE_TYPE]
        )

    def _on_game_ended(self, frame: Dict[str, Any]) -> None:
        self._game_over = True
        self._game_over_reason = frame.get(protocol.FIELD_REASON)
        self._winner = frame.get(protocol.FIELD_WINNER)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def project(self) -> Optional[GameSnapshot]:
        """The board as it stands right now, with the clock advanced locally.

        Returns None until the first snapshot arrives.
        """
        if not self._has_state:
            return None

        clock_ms = self._projected_clock_ms()
        self._settle_arrived_movements(clock_ms)
        pieces = {
            square: self._piece_at_clock(square, piece, clock_ms)
            for square, piece in self._pieces.items()
        }

        return GameSnapshot(
            rows=self._rows,
            cols=self._cols,
            pieces=pieces,
            selected_pos=self._selected_pos,
            legal_move_targets=self._legal_move_targets,
            castle_targets=self._castle_targets,
            active_movements=tuple(self._movements.values()),
            cooldown_positions=tuple(
                square for square in self._cooldown_until if square in pieces
            ),
            clock_ms=clock_ms,
            game_over=self._game_over,
            game_over_reason=self._game_over_reason,
            winner=self._winner,
        )

    def _projected_clock_ms(self) -> int:
        """The game clock, extrapolated from wall time since the last anchor.

        The game clock only advances while the game runs, so it is frozen once
        the game is over — extrapolating past the end would keep animating
        cooldowns on a finished board.
        """
        if self._game_over:
            return self._anchor_clock_ms
        elapsed_seconds = max(0.0, self._time_fn() - self._anchor_wall_seconds)
        return self._anchor_clock_ms + int(elapsed_seconds * consts.MS_PER_SECOND)

    def _settle_arrived_movements(self, clock_ms: int) -> None:
        """Land any movement whose arrival time has passed without a confirming event.

        A predicted landing keeps a piece visible and in place instead of
        freezing it on its origin square until the network catches up. The
        server's own `event_piece_moved` re-lands it identically, and a capture
        that happened instead is corrected within one reconciliation interval.
        """
        for origin, movement in list(self._movements.items()):
            if movement.arrival_ms <= clock_ms:
                self._land(origin, movement.to, movement.arrival_ms, None)

    def _piece_at_clock(
        self, square: Position, piece: PieceSnapshot, clock_ms: int
    ) -> PieceSnapshot:
        """Recompute one piece's animation state against the projected clock."""
        movement = self._movements.get(square)
        if movement is not None:
            return self._with_timing(
                piece,
                elapsed=max(0, clock_ms - movement.start_ms),
                duration=max(0, movement.arrival_ms - movement.start_ms),
            )

        cooldown_end = self._cooldown_until.get(square)
        if cooldown_end is None:
            return piece
        remaining = cooldown_end - clock_ms
        if remaining <= 0:
            self._cooldown_until.pop(square, None)
            return PieceSnapshot(
                color=piece.color,
                piece_type=piece.piece_type,
                has_moved=piece.has_moved,
                can_select=True,
                can_move=True,
                state=PieceVisualState.IDLE,
                state_elapsed_millis=_NO_DURATION,
                state_duration_millis=_NO_DURATION,
            )
        return self._with_timing(
            piece,
            elapsed=max(0, self._cooldown_duration_ms - remaining),
            duration=self._cooldown_duration_ms,
        )

    @staticmethod
    def _with_timing(piece: PieceSnapshot, elapsed: int, duration: int) -> PieceSnapshot:
        return PieceSnapshot(
            color=piece.color,
            piece_type=piece.piece_type,
            has_moved=piece.has_moved,
            can_select=piece.can_select,
            can_move=piece.can_move,
            state=piece.state,
            state_elapsed_millis=elapsed,
            state_duration_millis=duration,
        )

    @staticmethod
    def _with_piece_type(piece: PieceSnapshot, piece_type: str) -> PieceSnapshot:
        return PieceSnapshot(
            color=piece.color,
            piece_type=piece_type,
            has_moved=piece.has_moved,
            can_select=piece.can_select,
            can_move=piece.can_move,
            state=piece.state,
            state_elapsed_millis=piece.state_elapsed_millis,
            state_duration_millis=piece.state_duration_millis,
        )

    def _anchor(self, clock_ms: int) -> None:
        """Peg the projected clock to *clock_ms* at this instant of wall time."""
        self._anchor_clock_ms = clock_ms
        self._anchor_wall_seconds = self._time_fn()

    def _advance_anchor_to(self, at_ms: Any) -> None:
        """Move the clock anchor forward to an event's timestamp.

        Forward only: the simulation clock never flows backward, and an event
        that arrives out of order must not drag the projection back with it.
        """
        if not isinstance(at_ms, int):
            return
        if at_ms > self._projected_clock_ms():
            self._anchor(at_ms)


def projected_squares(snapshot: GameSnapshot) -> List[Position]:
    """Every occupied square in *snapshot*, for tests and debugging."""
    return sorted(snapshot.pieces, key=lambda pos: (pos.row, pos.col))
