"""Tkinter window — concrete ImageViewInterface plus the mouse/timer input loop (Layer 6/7).

Owns: the tkinter Canvas, translating mouse clicks into GameEngine calls, and
driving the render loop (advance clock -> build snapshot -> draw -> show)
once per tick. Mirrors python_port's GameController one-to-one: every pixel
shown ever came from an Img composed by PillowRenderer, tkinter here only
displays the already-finished frame.
Must not own: game rules, board mutation, or pixel drawing (PillowRenderer
owns drawing; GameEngine owns rules/mutation).
"""

import time
import tkinter as tk

from PIL import ImageTk

from kungfu_chess.engine.game_engine import GameEngine
from kungfu_chess.engine.engine_interfaces import BoardRepositoryInterface, GameStateRepositoryInterface
from kungfu_chess.model.position import Position
from kungfu_chess.view.image_view import ImageViewInterface
from kungfu_chess.view.pillow_renderer import PillowRenderer
from kungfu_chess.view.snapshot_builder import SnapshotBuilder

TICK_MS = 16
BOARD_SIZE = 640


class TkImageView(ImageViewInterface):
    """Displays an already-rendered Img on a tkinter Canvas."""

    def __init__(self, canvas: tk.Canvas, canvas_image_id: int):
        self._canvas = canvas
        self._canvas_image_id = canvas_image_id
        self._tk_image = None  # keep a reference alive; tkinter drops GC'd images

    def show(self, image: object) -> None:
        self._tk_image = ImageTk.PhotoImage(image.get())
        self._canvas.itemconfig(self._canvas_image_id, image=self._tk_image)


class TkGameWindow:
    """Owns the tkinter Tk root, canvas, click bindings, and tick loop."""

    def __init__(
        self,
        engine: GameEngine,
        board_repo: BoardRepositoryInterface,
        state_repo: GameStateRepositoryInterface,
        renderer: PillowRenderer,
        snapshot_builder: SnapshotBuilder,
        title: str = "Kung Fu Chess",
        board_size: int = BOARD_SIZE,
    ):
        self.engine = engine
        self.board_repo = board_repo
        self.state_repo = state_repo
        self.renderer = renderer
        self.snapshot_builder = snapshot_builder
        self.board_size = board_size

        self.root = tk.Tk()
        self.root.title(title)

        self.canvas = tk.Canvas(self.root, width=board_size, height=board_size, highlightthickness=0)
        self.canvas.pack()

        canvas_image_id = self.canvas.create_image(0, 0, anchor="nw")
        self.view = TkImageView(self.canvas, canvas_image_id)

        self.renderer.resize(board_size, board_size)

        self.canvas.bind("<Button-1>", self._on_left_click)
        self.canvas.bind("<Button-3>", self._on_right_click)

        self._last_tick = time.monotonic()
        self._refresh()
        self._schedule_tick()

    def run(self) -> None:
        self.root.mainloop()

    # -- input ------------------------------------------------------------

    def _canvas_to_cell(self, event_x: int, event_y: int) -> tuple[int, int] | None:
        return self.renderer.get_geometry().pixel_to_cell(event_x, event_y)

    def _on_left_click(self, event) -> None:
        cell = self._canvas_to_cell(event.x, event.y)
        if cell is None:
            return
        row, col = cell
        target = Position(row, col)

        state = self.state_repo.get_state()
        selected = state.selected_pos
        if selected is not None:
            self.engine.request_move(selected, target)
        else:
            board = self.board_repo.get_board()
            piece = board.get_piece(target) if board is not None else None
            if piece is not None and piece.can_select():
                state.selected_pos = target
                self.state_repo.save_state(state)
        self._refresh()

    def _on_right_click(self, event) -> None:
        """Jump the piece under the cursor in place, regardless of current selection."""
        cell = self._canvas_to_cell(event.x, event.y)
        if cell is None:
            return
        row, col = cell
        target = Position(row, col)
        self.engine.request_move(target, target)
        self._refresh()

    # -- tick loop ----------------------------------------------------------

    def _schedule_tick(self) -> None:
        self.root.after(TICK_MS, self._tick)

    def _tick(self) -> None:
        now = time.monotonic()
        elapsed_ms = int((now - self._last_tick) * 1000)
        self._last_tick = now

        self.engine.advance_clock(elapsed_ms)
        self._refresh()
        self._schedule_tick()

    # -- rendering ----------------------------------------------------------

    def _refresh(self) -> None:
        board = self.board_repo.get_board()
        if board is None:
            return
        state = self.state_repo.get_state()
        snapshot = self.snapshot_builder.build(board, state)
        self.renderer.draw(snapshot)
        self.view.show(self.renderer.get_image())
