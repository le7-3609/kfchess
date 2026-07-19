"""Unit tests for InfoPanel — the side-panel chrome composed around the board.

The panel caches its chrome between frames because rasterizing text is far more
expensive than drawing the board itself. These tests pin down that the cache is
invisible: a cached panel must paint what a freshly built one would, and must
turn over whenever the moves or the canvas size change. A stale cache would show
the wrong move list rather than crash, so nothing else would catch it.
"""

import unittest

from kungfu_chess.io.moves_log import MoveLogEntry
from kungfu_chess.ui.rendering.img import Img
from kungfu_chess.ui.rendering.info_panel import InfoPanel
from kungfu_chess.config.consts import PANEL_MAX_ROWS as MAX_ROWS, SIDE_PANEL_WIDTH, PANEL_TOP_HEIGHT as TOP_HEIGHT

BOARD_SIZE = 64
CANVAS_W = SIDE_PANEL_WIDTH * 2 + BOARD_SIZE
CANVAS_H = TOP_HEIGHT + BOARD_SIZE


def _board(color=(10, 120, 10, 255)) -> Img:
    return Img().blank(BOARD_SIZE, BOARD_SIZE, color)


def _entry(color: str, notation: str, time_ms: int) -> MoveLogEntry:
    return MoveLogEntry(color=color, notation=notation, time_ms=time_ms)


def _same_pixels(first: Img, second: Img) -> bool:
    """Exact pixel equality.

    Deliberately not ImageChops.difference(...).getbbox(): on an RGBA image
    getbbox() reads the alpha channel alone, so two fully opaque images that
    differ in every color channel still report no bounding box. Comparing the
    raw bytes is exact and catches color-only differences, which is the whole
    point here — the panel's ink is opaque.
    """
    left, right = first.get(), second.get()
    return left.size == right.size and left.tobytes() == right.tobytes()


def _render(panel: InfoPanel, moves, width=CANVAS_W, height=CANVAS_H, board=None) -> Img:
    return panel.render(board or _board(), BOARD_SIZE, width, height, moves)


def _fresh_render(moves, width=CANVAS_W, height=CANVAS_H, board=None) -> Img:
    """What a panel with no cache at all would paint — the reference."""
    return _render(InfoPanel("White", "Black"), moves, width, height, board)


class TestChromeCacheIsInvisible(unittest.TestCase):
    """A reused panel must paint exactly what a brand-new one would."""

    def setUp(self):
        self.panel = InfoPanel("White", "Black")
        self.moves = [_entry("w", "Nf3", 1000), _entry("b", "Nc6", 1800)]

    def test_repeated_render_of_the_same_frame_is_stable(self):
        first = _render(self.panel, self.moves)
        second = _render(self.panel, self.moves)
        self.assertTrue(_same_pixels(first, second))

    def test_cached_render_matches_an_uncached_one(self):
        _render(self.panel, self.moves)  # prime the cache
        self.assertTrue(_same_pixels(_render(self.panel, self.moves), _fresh_render(self.moves)))

    def test_a_new_move_invalidates_the_cache(self):
        _render(self.panel, self.moves)
        grown = self.moves + [_entry("w", "Bb5", 2600)]
        self.assertTrue(_same_pixels(_render(self.panel, grown), _fresh_render(grown)))

    def test_a_shrinking_move_list_invalidates_the_cache(self):
        """A replay scrubbed backwards hands back a shorter list."""
        _render(self.panel, self.moves)
        self.assertTrue(_same_pixels(_render(self.panel, self.moves[:1]), _fresh_render(self.moves[:1])))

    def test_a_move_list_of_the_same_length_but_different_content_invalidates(self):
        """Keying on length alone would wrongly reuse the chrome here."""
        _render(self.panel, self.moves)
        swapped = [_entry("w", "e4", 1000), _entry("b", "e5", 1800)]
        self.assertTrue(_same_pixels(_render(self.panel, swapped), _fresh_render(swapped)))

    def test_a_resize_invalidates_the_cache(self):
        _render(self.panel, self.moves)
        wider, taller = CANVAS_W + 90, CANVAS_H + 40
        resized = _render(self.panel, self.moves, wider, taller)
        self.assertEqual(resized.get().size, (wider, taller))
        self.assertTrue(_same_pixels(resized, _fresh_render(self.moves, wider, taller)))

    def test_only_the_drawn_rows_matter(self):
        """Moves scrolled past MAX_ROWS are not drawn, so they must not force a
        rebuild — but the cache must not go stale for the rows that are."""
        many = [_entry("w", f"a{i}", i * 100) for i in range(MAX_ROWS * 2)]
        _render(self.panel, many)
        self.assertTrue(_same_pixels(_render(self.panel, many), _fresh_render(many)))


class TestScores(unittest.TestCase):
    """Scores arrive from ScoreUpdatedEvent and must repaint the chrome."""

    def setUp(self):
        self.panel = InfoPanel("White", "Black")
        self.moves = [_entry("w", "Nf3", 1000)]

    def _render_scored(self, white, black, panel=None):
        return (panel or self.panel).render(
            _board(), BOARD_SIZE, CANVAS_W, CANVAS_H, self.moves,
            white_score=white, black_score=black,
        )

    def test_a_score_change_invalidates_the_cache(self):
        self._render_scored(0, 0)
        self.assertTrue(_same_pixels(
            self._render_scored(3, 0), self._render_scored(3, 0, InfoPanel("White", "Black"))
        ))

    def test_different_scores_paint_differently(self):
        self.assertFalse(_same_pixels(self._render_scored(0, 0), self._render_scored(9, 0)))

    def test_each_side_gets_its_own_score(self):
        self.assertFalse(_same_pixels(self._render_scored(5, 0), self._render_scored(0, 5)))

    def test_omitting_scores_paints_no_score_line(self):
        """The replay window has no captures to total, so it passes none."""
        self.assertFalse(_same_pixels(_render(self.panel, self.moves), self._render_scored(0, 0)))


class TestChromeCacheDoesNotLeakBetweenFrames(unittest.TestCase):
    def test_the_returned_image_is_not_the_cached_chrome(self):
        """render must hand back a copy: the caller gets a board drawn over the
        chrome, and mutating that must not poison the next frame."""
        panel = InfoPanel("White", "Black")
        moves = [_entry("w", "Nf3", 1000)]
        first = _render(panel, moves, board=_board((255, 0, 0, 255)))
        first.fill_rect(0, 0, SIDE_PANEL_WIDTH, TOP_HEIGHT, (0, 0, 255, 255))

        self.assertTrue(_same_pixels(_render(panel, moves, board=_board((255, 0, 0, 255))), _fresh_render(moves, board=_board((255, 0, 0, 255)))))

    def test_a_new_board_shows_through_on_the_next_frame(self):
        """The board changes every frame even when the chrome does not."""
        panel = InfoPanel("White", "Black")
        moves = [_entry("w", "Nf3", 1000)]
        _render(panel, moves, board=_board((255, 0, 0, 255)))
        second = _render(panel, moves, board=_board((0, 255, 0, 255)))

        board_x = SIDE_PANEL_WIDTH + (CANVAS_W - SIDE_PANEL_WIDTH * 2 - BOARD_SIZE) // 2
        board_y = TOP_HEIGHT + (CANVAS_H - TOP_HEIGHT - BOARD_SIZE) // 2
        self.assertEqual(second.get().getpixel((board_x + 5, board_y + 5)), (0, 255, 0, 255))


if __name__ == "__main__":
    unittest.main()
