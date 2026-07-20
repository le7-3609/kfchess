"""Board geometry — cell <-> pixel mapping for on-screen rendering (Layer 6).

Owns: mapping between board (row, col) cells and screen pixel rectangles for
a board rendered into an arbitrary panel size.
Must not own: game rules, board mutation, input parsing, or timing.
"""

from dataclasses import dataclass


@dataclass
class Rectangle:
    x: int
    y: int
    width: int
    height: int


class BoardGeometry:
    def __init__(self, rows: int, cols: int):
        self.rows = rows
        self.cols = cols
        self.board_size = 0
        self.origin_x = 0
        self.origin_y = 0
        self.flipped = False

    def set_flipped(self, flipped: bool) -> None:
        self.flipped = flipped

    def resize(self, panel_width: int, panel_height: int) -> None:
        self.board_size = min(panel_width, panel_height)
        self.origin_x = (panel_width - self.board_size) // 2
        self.origin_y = (panel_height - self.board_size) // 2

    def get_cell_width(self) -> float:
        return self.board_size / self.cols

    def get_cell_height(self) -> float:
        return self.board_size / self.rows

    def cell_to_pixel(self, row: int, col: int) -> Rectangle:
        cw = self.get_cell_width()
        ch = self.get_cell_height()

        target_row = self.rows - 1 - row if self.flipped else row
        target_col = self.cols - 1 - col if self.flipped else col

        x = self.origin_x + round(target_col * cw)
        w = self.origin_x + round((target_col + 1) * cw) - x
        y = self.origin_y + round(target_row * ch)
        h = self.origin_y + round((target_row + 1) * ch) - y
        return Rectangle(x, y, w, h)

    def pixel_to_cell(self, px: int, py: int) -> tuple[int, int] | None:
        if self.board_size == 0:
            return None
        if (
            px < self.origin_x
            or py < self.origin_y
            or px >= self.origin_x + self.board_size
            or py >= self.origin_y + self.board_size
        ):
            return None

        col = int((px - self.origin_x) / self.get_cell_width())
        row = int((py - self.origin_y) / self.get_cell_height())
        col = min(max(col, 0), self.cols - 1)
        row = min(max(row, 0), self.rows - 1)

        if self.flipped:
            row = self.rows - 1 - row
            col = self.cols - 1 - col

        return row, col

