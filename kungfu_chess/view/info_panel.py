"""InfoPanel — side-panel chrome around the board (Layer 6).

Owns: composing the board image with player-name headers and each player's
live "last moves" list drawn underneath their name, mirroring python_port's
view/info_panel.py.
Must not own: game rules, board mutation, or pixel drawing of the board
itself (PillowRenderer owns that; this only composes around it).
"""

from kungfu_chess.io.moves_log import MovesLog
from kungfu_chess.view.img import Img

BACKGROUND_COLOR = (45, 45, 45, 255)
TEXT_COLOR = (235, 235, 235, 255)
SIDE_PANEL_WIDTH = 220
TOP_HEIGHT = 50
ROW_HEIGHT = 20
MAX_ROWS = 12

_COLOR_NAMES = {"w": "White", "b": "Black"}


def _format_time(millis: int) -> str:
    total_seconds = millis // 1000
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    ms = millis % 1000
    return f"{minutes:02d}:{seconds:02d}.{ms:03d}"


class InfoPanel:
    """Wraps a rendered board Img with a name + last-moves column per side."""

    def __init__(self, white_name: str, black_name: str, moves_log: MovesLog):
        self.white_name = white_name
        self.black_name = black_name
        self.moves_log = moves_log

    def render(self, board_img: Img, board_size: int) -> Img:
        total_width = SIDE_PANEL_WIDTH * 2 + board_size
        total_height = TOP_HEIGHT + board_size

        img = Img().blank(total_width, total_height, BACKGROUND_COLOR)
        board_img.draw_on(img, SIDE_PANEL_WIDTH, TOP_HEIGHT)

        self._draw_moves_column(img, 0, self.white_name, "w")
        self._draw_moves_column(img, SIDE_PANEL_WIDTH + board_size, self.black_name, "b")

        return img

    def _draw_moves_column(self, img: Img, x: int, name: str, color: str) -> None:
        img.put_text(name, x + SIDE_PANEL_WIDTH // 2, 12, 16, TEXT_COLOR, anchor="mt")

        entries = self.moves_log.entries_for(color)[-MAX_ROWS:]
        y = TOP_HEIGHT
        for entry in entries:
            row_text = f"{_format_time(entry.time_ms)}  {entry.notation}"
            img.put_text(row_text, x + 10, y, 11, TEXT_COLOR, anchor="lt")
            y += ROW_HEIGHT
