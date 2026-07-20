"""Replay window — visual playback of a SavedGame (UI window/controls layer).

Owns: reconstructing a saved move list into per-frame GameSnapshots, and the
tkinter window/controls that play them back.
Must not own: game rules. Playback deliberately never runs the arbiter — a
saved log holds only moves that *did* resolve, so re-deriving them through the
collision/legality machinery could abort a move the log says succeeded. The
director simply replays what was recorded.

Reconstructing the timeline
---------------------------
MovesLog records a move when it *arrives*, so a move's start is recovered as
``arrival - travel time``, where travel time comes from the distance and the
speed the save was written with. Two things follow from the log holding only
resolved arrivals, and neither is recoverable from the save format:

  - A piece captured mid-flight never arrived, so it was never logged. In
    replay it sits still on its origin square until its captor reaches it.
  - A move whose path was blocked was parked short of its target without ever
    publishing, so replay leaves that piece on its origin square. The final
    replayed board can therefore differ from the real one in such a game.

Both degrade to a piece that under-moves; neither can desync the rest of the
playback, since every other move is replayed from its own recorded squares.
"""

import time
import tkinter as tk
from dataclasses import dataclass, replace
from typing import Dict, List, Optional

from shared.config import consts
from shared.config.game_config import GameConfig
from shared.io.board_parser import BoardParser
from shared.io.game_history_store import SavedGame
from shared.io.moves_log import MoveLogEntry, parse_notation
from shared.model.piece import PieceFactory
from shared.model.position import Position
from client.ui import consts as ui_consts
from client.ui.rendering.info_panel import InfoPanel
from client.ui.rendering.pillow_renderer import PillowRenderer
from client.ui.window.image_view import TkImageView
from shared.view.game_snapshot import GameSnapshot, MovementSnapshot, PieceSnapshot
from shared.view.piece_visual_state import PieceVisualState


@dataclass(frozen=True)
class _ReplayPiece:
    """A piece on the replay's own board. Deliberately not model.piece: replay
    tracks no lifecycle state, deriving visuals from the timeline instead."""

    color: str
    piece_type: str
    has_moved: bool = False


@dataclass(frozen=True)
class ReplayMove:
    """One logged move, with its start time reconstructed."""

    color: str
    piece_type: str  # type on arrival — a promoting pawn arrives as its promotion
    frm: Position
    to: Position
    start_ms: int
    arrival_ms: int

    @property
    def is_jump(self) -> bool:
        """A jump-in-place (right-click): it lifts and lands on one square."""
        return self.frm == self.to


def _starting_pieces() -> Dict[Position, _ReplayPiece]:
    """The standard opening setup, read through the same parser the live game uses."""
    rows, _ = BoardParser().parse(consts.STARTING_POSITION.splitlines())
    pieces: Dict[Position, _ReplayPiece] = {}
    for row_index, tokens in enumerate(rows):
        for col_index, token in enumerate(tokens):
            piece = PieceFactory.from_string(token)
            if piece is not None:
                pieces[Position(row_index, col_index)] = _ReplayPiece(piece.color, piece.piece_type)
    return pieces


def _travel_ms(move_from: Position, move_to: Position, speed_ms: int, jump_ms: int) -> int:
    if move_from == move_to:
        return jump_ms  # a jump takes a fixed time; it covers no distance
    distance = max(abs(move_to.row - move_from.row), abs(move_to.col - move_from.col))
    return distance * speed_ms


def reconstruct_moves(saved: SavedGame, config: GameConfig) -> List[ReplayMove]:
    """Turn a save's notation entries into moves with start times, arrival-ordered.

    Unparseable entries are dropped rather than raising: saves are plain JSON on
    disk and a single bad line should not cost the whole replay.
    """
    moves: List[ReplayMove] = []
    for entry in saved.moves:
        parsed = parse_notation(entry.notation)
        if parsed is None:
            continue
        duration = _travel_ms(parsed.frm, parsed.to, saved.speed_ms, config.jump_duration_ms)
        moves.append(
            ReplayMove(
                color=entry.color,
                piece_type=parsed.piece_type,
                frm=parsed.frm,
                to=parsed.to,
                start_ms=entry.time_ms - duration,
                arrival_ms=entry.time_ms,
            )
        )
    moves.sort(key=lambda move: move.arrival_ms)
    return moves


class ReplayDirector:
    """Rebuilds the board as it stood at any instant of a saved game.

    ``snapshot_at`` is a pure function of the timeline, so seeking backwards
    costs no more than playing forwards and cannot drift from the log.
    """

    def __init__(self, saved: SavedGame, config: Optional[GameConfig] = None) -> None:
        self._saved = saved
        self._config = config or GameConfig()
        self._initial = _starting_pieces()
        self._moves = reconstruct_moves(saved, self._config)
        self._cooldown_ms = saved.cooldown_ms
        last_arrival = max((move.arrival_ms for move in self._moves), default=0)
        self.duration_ms = last_arrival + ui_consts.REPLAY_END_PAD_MS

    @property
    def moves(self) -> List[ReplayMove]:
        return list(self._moves)

    def snapshot_at(self, clock_ms: int) -> GameSnapshot:
        """Build the render DTO for the board as it stood at *clock_ms*.

        Clamps *clock_ms* into the replay's range, so seeking past either end
        yields the first or last frame rather than an empty board.
        """
        clock_ms = max(0, min(self.duration_ms, clock_ms))
        pieces, landed, in_flight = self._replay_up_to(clock_ms)

        flying_from = {move.frm: move for move in in_flight}
        piece_snapshots = {
            pos: self._piece_snapshot(pos, piece, clock_ms, flying_from.get(pos), landed.get(pos))
            for pos, piece in pieces.items()
        }

        finished = clock_ms >= self.duration_ms
        return GameSnapshot(
            rows=self._config.board_rows,
            cols=self._config.board_cols,
            pieces=piece_snapshots,
            selected_pos=None,
            legal_move_targets=(),
            castle_targets=(),
            active_movements=self._build_movements(in_flight, piece_snapshots),
            cooldown_positions=self._cooldown_positions(landed, clock_ms),
            clock_ms=clock_ms,
            # A save taken mid-game has no winner; only a finished game earns the banner.
            game_over=finished and self._saved.winner is not None,
            game_over_reason=None,
            winner=self._saved.winner,
        )

    def _replay_up_to(self, clock_ms: int):
        """Replay the log onto the opening setup, stopping at *clock_ms*.

        Returns (pieces by square, most recent arrival per square, moves still
        in flight). The log is arrival-ordered, so applying it in sequence
        reconstructs the board correctly without sorting.
        """
        pieces = dict(self._initial)
        landed: Dict[Position, ReplayMove] = {}
        in_flight: List[ReplayMove] = []

        for move in self._moves:
            if move.arrival_ms <= clock_ms:
                self._apply(pieces, landed, move)
            elif move.start_ms <= clock_ms:
                in_flight.append(move)
        return pieces, landed, in_flight

    def _build_movements(self, in_flight: List[ReplayMove], piece_snapshots: dict) -> tuple:
        """Wrap each in-flight move as a MovementSnapshot the Renderer can interpolate."""
        return tuple(
            MovementSnapshot(
                frm=move.frm,
                to=move.to,
                piece=piece_snapshots[move.frm],
                start_ms=move.start_ms,
                arrival_ms=move.arrival_ms,
            )
            for move in in_flight
            if move.frm in piece_snapshots
        )

    def _cooldown_positions(self, landed: Dict[Position, ReplayMove], clock_ms: int) -> tuple:
        """Return the squares whose last arrival is still within the cooldown window."""
        return tuple(
            pos for pos, move in landed.items()
            if clock_ms - move.arrival_ms < self._cooldown_ms
        )

    def moves_until(self, clock_ms: int) -> List[MoveLogEntry]:
        """The log entries visible at *clock_ms*, for the side panel."""
        return [entry for entry in self._saved.moves if entry.time_ms <= clock_ms]

    def _apply(self, pieces: Dict[Position, _ReplayPiece], landed: Dict[Position, ReplayMove], move: ReplayMove) -> None:
        mover = pieces.get(move.frm)
        # The mover can be missing where the log has a gap (see module docstring):
        # skip rather than fabricate a piece the save never described.
        if mover is None or mover.color != move.color:
            return

        if move.is_jump:
            landed[move.to] = move
            return

        pieces.pop(move.frm, None)
        landed.pop(move.frm, None)
        self._apply_en_passant(pieces, landed, move, mover)
        pieces.pop(move.to, None)
        landed.pop(move.to, None)
        # Taking the type from the log rather than the mover is what replays a
        # promotion: the pawn is logged arriving as the piece it promoted into.
        pieces[move.to] = replace(mover, piece_type=move.piece_type, has_moved=True)
        landed[move.to] = move

    def _apply_en_passant(
        self,
        pieces: Dict[Position, _ReplayPiece],
        landed: Dict[Position, ReplayMove],
        move: ReplayMove,
        mover: _ReplayPiece,
    ) -> None:
        """Remove a pawn taken en passant.

        The victim stands beside the capturer, not on its destination, so it is
        never named by the log — but a pawn stepping diagonally onto an empty
        square can only be en passant, which identifies it.
        """
        is_diagonal_step = move.frm.col != move.to.col
        if mover.piece_type != consts.PIECE_PAWN or not is_diagonal_step or move.to in pieces:
            return
        victim_pos = Position(move.frm.row, move.to.col)
        victim = pieces.get(victim_pos)
        is_enemy_pawn = victim is not None and victim.color != mover.color and victim.piece_type == consts.PIECE_PAWN
        if is_enemy_pawn:
            pieces.pop(victim_pos, None)
            landed.pop(victim_pos, None)

    def _piece_snapshot(
        self,
        pos: Position,
        piece: _ReplayPiece,
        clock_ms: int,
        flying: Optional[ReplayMove],
        landed: Optional[ReplayMove],
    ) -> PieceSnapshot:
        state = PieceVisualState.IDLE
        elapsed = 0
        duration = 0

        if flying is not None:
            state = PieceVisualState.JUMP if flying.is_jump else PieceVisualState.MOVE
            elapsed = clock_ms - flying.start_ms
            duration = flying.arrival_ms - flying.start_ms
        elif landed is not None and clock_ms - landed.arrival_ms < self._cooldown_ms:
            state = PieceVisualState.SHORT_REST
            elapsed = clock_ms - landed.arrival_ms
            duration = self._cooldown_ms

        return PieceSnapshot(
            color=piece.color,
            piece_type=piece.piece_type,
            has_moved=piece.has_moved,
            can_select=False,  # a replay is a recording, not a game
            can_move=False,
            state=state,
            state_elapsed_millis=max(0, elapsed),
            state_duration_millis=duration,
        )


def _format_clock(millis: int) -> str:
    total_seconds = millis // consts.MS_PER_SECOND
    minutes = total_seconds // consts.SECONDS_PER_MINUTE
    seconds = total_seconds % consts.SECONDS_PER_MINUTE
    return f"{minutes:02d}:{seconds:02d}.{millis % consts.MS_PER_SECOND:03d}"


class TkReplayWindow:
    """A Toplevel that plays a SavedGame back, with play/pause and scrubbing.

    Takes a renderer of its own rather than borrowing the game window's: the
    two size their boards independently and would otherwise fight over it.
    """

    def __init__(
        self,
        parent: tk.Misc,
        saved: SavedGame,
        renderer: PillowRenderer,
        config: Optional[GameConfig] = None,
        board_size: int = ui_consts.BOARD_SIZE,
    ) -> None:
        self.director = ReplayDirector(saved, config)
        self.renderer = renderer
        self.board_size = board_size
        self.info_panel = InfoPanel(
            saved.white_name or ui_consts.DEFAULT_WHITE_NAME,
            saved.black_name or ui_consts.DEFAULT_BLACK_NAME,
        )

        self.clock_ms = 0
        self._playing = True
        self._closed = False
        self._syncing_scrubber = False
        # Sub-millisecond remainder carried between ticks; dropping it would
        # lose most of a millisecond per frame and drag playback slow again.
        self._clock_remainder_ms = 0.0
        self._last_tick = 0.0

        self.window = tk.Toplevel(parent)
        self.window.title(f"Replay: {saved.save_name}")
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_canvas(board_size)
        self._build_controls(saved)
        self._start_playback()

    def _build_canvas(self, board_size: int) -> None:
        """Create the canvas, its image view, and the resize binding."""
        self.canvas_width = ui_consts.SIDE_PANEL_WIDTH * 2 + board_size
        self.canvas_height = ui_consts.PANEL_TOP_HEIGHT + board_size
        self.canvas = tk.Canvas(
            self.window, width=self.canvas_width, height=self.canvas_height, highlightthickness=0
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>", self._on_resize)

        canvas_image_id = self.canvas.create_image(0, 0, anchor="nw")
        self.view = TkImageView(self.canvas, canvas_image_id)
        self.renderer.resize(board_size, board_size)

    def _start_playback(self) -> None:
        """Draw the first frame, then start the replay clock and tick loop.

        The clock starts only once that frame is up: building the window and
        priming the render caches must not count as replay time.
        """
        self._refresh()
        self._last_tick = time.monotonic()
        self._schedule_tick()

    def _build_controls(self, saved: SavedGame) -> None:
        controls = tk.Frame(self.window)
        controls.pack(fill=tk.X, padx=10, pady=(0, 10))

        self._play_button = tk.Button(controls, text="Pause", width=8, command=self._toggle_play)
        self._play_button.pack(side=tk.LEFT)
        tk.Button(controls, text="Restart", width=8, command=self._restart).pack(side=tk.LEFT, padx=(5, 10))

        self._scrubber_var = tk.IntVar(master=self.window, value=0)
        self._scrubber = tk.Scale(
            controls,
            from_=0,
            to=max(ui_consts.REPLAY_MIN_SCRUBBER_MS, self.director.duration_ms),
            orient=tk.HORIZONTAL,
            showvalue=False,
            variable=self._scrubber_var,
            command=self._on_scrub,
        )
        self._scrubber.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._time_label = tk.Label(controls, text=_format_clock(0), width=10)
        self._time_label.pack(side=tk.LEFT, padx=(10, 0))

        header = f"{saved.white_name} vs {saved.black_name}    Saved: {saved.saved_at}"
        if saved.winner:
            winner_name = ui_consts.COLOR_DISPLAY_NAMES.get(saved.winner, saved.winner)
            header += f"    Winner: {winner_name}"
        tk.Label(self.window, text=header).pack(pady=(0, 8))

    def run(self) -> None:
        """Block until the replay window closes. Only for standalone use — when
        opened from the game window, its mainloop already drives this one."""
        self.window.wait_window()

    def _toggle_play(self) -> None:
        # Replaying from the end would otherwise sit frozen on the last frame.
        if not self._playing and self.clock_ms >= self.director.duration_ms:
            self.clock_ms = 0
        self._playing = not self._playing
        self._play_button.config(text="Pause" if self._playing else "Play")

    def _restart(self) -> None:
        self.clock_ms = 0
        self._playing = True
        self._play_button.config(text="Pause")
        self._refresh()

    def _on_scrub(self, value: str) -> None:
        if self._syncing_scrubber:  # our own playback update, not a user drag
            return
        self.clock_ms = int(value)
        self._refresh()

    def _on_close(self) -> None:
        self._closed = True  # stops the tick loop rescheduling onto a dead widget
        self.window.destroy()

    def _on_resize(self, event) -> None:
        if event.widget != self.canvas:
            return
        self.canvas_width = event.width
        self.canvas_height = event.height
        minimum = ui_consts.MIN_BOARD_DIMENSION_PX
        available_width = max(minimum, self.canvas_width - ui_consts.SIDE_PANEL_WIDTH * 2)
        available_height = max(minimum, self.canvas_height - ui_consts.PANEL_TOP_HEIGHT)
        self.board_size = min(available_width, available_height)
        self.renderer.resize(self.board_size, self.board_size)
        self._refresh()

    def _schedule_tick(self) -> None:
        if not self._closed:
            self.window.after(ui_consts.TICK_MS, self._tick)

    def _tick(self) -> None:
        if self._closed:
            return
        # Advance by the wall clock, not the nominal tick: `after` waits *at
        # least* TICK_MS and a refresh costs more on top, so crediting the clock
        # a flat TICK_MS per frame would play the recording back slower than it
        # was recorded, by however long the renderer happens to take.
        now = time.monotonic()
        elapsed_ms = (now - self._last_tick) * consts.MS_PER_SECOND + self._clock_remainder_ms
        self._last_tick = now
        if self._playing:
            step = int(elapsed_ms)
            self._clock_remainder_ms = elapsed_ms - step
            self.clock_ms = min(self.director.duration_ms, self.clock_ms + step)
            if self.clock_ms >= self.director.duration_ms:
                self._playing = False
                self._play_button.config(text="Play")
            self._refresh()
        self._schedule_tick()

    def _refresh(self) -> None:
        snapshot = self.director.snapshot_at(self.clock_ms)
        self.renderer.draw(snapshot)
        composed = self.info_panel.render(
            self.renderer.get_image(),
            self.board_size,
            self.canvas_width,
            self.canvas_height,
            self.director.moves_until(self.clock_ms),
        )
        self.view.show(composed)

        self._syncing_scrubber = True
        self._scrubber_var.set(self.clock_ms)
        self._syncing_scrubber = False
        self._time_label.config(text=_format_clock(self.clock_ms))
