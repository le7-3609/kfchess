"""InfoPanel — side-panel chrome around the board (UI rendering layer).

Owns: composing the board image with player-name headers, each player's
captured-material score, and their live "last moves" list.
Must not own: game rules, board mutation, move recording, or pixel drawing of
the board itself (PillowRenderer owns that; this only composes around it). The
moves it draws are handed in per frame as read-only MoveLogEntry DTOs, so this
layer never holds an application object.

The chrome is cached between frames. Rasterizing a line of text costs about as
much as drawing the entire board, and the panel redraws a dozen of them, but its
content only changes when a move is logged or the window is resized — so the
composed background is kept and reused until one of those actually changes.
Caching is safe here precisely because the panel holds no application state: the
same inputs always paint the same pixels.
"""

from typing import Optional, Sequence, Tuple

from kungfu_chess.ui.rendering.img import Img
from kungfu_chess.io.moves_log import MoveLogEntry

BACKGROUND_COLOR = (45, 45, 45, 255)
TEXT_COLOR = (235, 235, 235, 255)
SCORE_COLOR = (150, 220, 150, 255)
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

    def __init__(self, white_name: str, black_name: str):
        self.white_name = white_name
        self.black_name = black_name
        self._chrome: Optional[Img] = None
        self._chrome_key: Optional[tuple] = None

    def render(
        self,
        board_img: Img,
        board_size: int,
        canvas_width: int,
        canvas_height: int,
        moves: Sequence[MoveLogEntry],
        white_score: Optional[int] = None,
        black_score: Optional[int] = None,
    ) -> Img:
        """Compose the chrome around *board_img*.

        Scores are optional: a caller with no score to show (the replay window,
        which reconstructs moves but not captures) omits them and gets a header
        with just the player's name.
        """
        img = self._chrome_for(canvas_width, canvas_height, moves, white_score, black_score).copy()

        board_x = SIDE_PANEL_WIDTH + (canvas_width - SIDE_PANEL_WIDTH * 2 - board_size) // 2
        board_y = TOP_HEIGHT + (canvas_height - TOP_HEIGHT - board_size) // 2

        board_img.draw_on(img, board_x, board_y)

        return img

    def _chrome_for(
        self,
        canvas_width: int,
        canvas_height: int,
        moves: Sequence[MoveLogEntry],
        white_score: Optional[int],
        black_score: Optional[int],
    ) -> Img:
        """The background and both move columns, rebuilt only when they change.

        The key holds the finished row text rather than the entries themselves,
        so it turns over exactly when the drawn pixels would differ — and a
        scrubbed-backwards replay, whose move list shrinks, invalidates it too.
        """
        white_rows = self._rows_for("w", moves)
        black_rows = self._rows_for("b", moves)
        key = (canvas_width, canvas_height, white_rows, black_rows, white_score, black_score)
        if self._chrome is None or self._chrome_key != key:
            chrome = Img().blank(canvas_width, canvas_height, BACKGROUND_COLOR)
            self._draw_moves_column(chrome, 0, self.white_name, white_score, white_rows)
            self._draw_moves_column(
                chrome, canvas_width - SIDE_PANEL_WIDTH, self.black_name, black_score, black_rows
            )
            self._chrome = chrome
            self._chrome_key = key
        return self._chrome

    def _rows_for(self, color: str, moves: Sequence[MoveLogEntry]) -> Tuple[str, ...]:
        return tuple(
            f"{_format_time(entry.time_ms)}  {entry.notation}"
            for entry in moves
            if entry.color == color
        )[-MAX_ROWS:]

    def _draw_moves_column(
        self, img: Img, x: int, name: str, score: Optional[int], rows: Tuple[str, ...]
    ) -> None:
        self._draw_column_header(img, x + SIDE_PANEL_WIDTH // 2, name, score)

        y = TOP_HEIGHT
        for row_text in rows:
            img.put_text(row_text, x + 10, y, 11, TEXT_COLOR, anchor="lt")
            y += ROW_HEIGHT

    def _draw_column_header(self, img: Img, center_x: int, name: str, score: Optional[int]) -> None:
        """Draw the player's name, with their score beneath it when there is one.

        Without a score the name is centred in the header band on its own;
        with one both are drawn higher so the pair fits the same band.
        """
        if score is None:
            img.put_text(name, center_x, 12, 16, TEXT_COLOR, anchor="mt")
            return
        img.put_text(name, center_x, 6, 16, TEXT_COLOR, anchor="mt")
        img.put_text(f"+{score}", center_x, 28, 12, SCORE_COLOR, anchor="mt")
