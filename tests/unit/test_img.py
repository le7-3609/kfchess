"""Unit tests for Img — the drawing primitive every rendered pixel goes through.

The blending tests matter because ImageDraw writes translucent colors straight
into an RGBA target rather than blending them; Img has to composite by hand, so
these pin that down.
"""

import unittest

from client.ui.rendering.img import Img

LIGHT = (240, 217, 181, 255)
HALF_BLACK = (0, 0, 0, 160)
# LIGHT under HALF_BLACK: 240 * (1 - 160/255) = 89, and so on per channel.
BLENDED = (89, 81, 67, 255)


def _blank(color=LIGHT, size=10) -> Img:
    return Img().blank(size, size, color)


class TestTranslucentFills(unittest.TestCase):
    def test_fill_rect_blends_with_what_is_underneath(self):
        img = _blank()
        img.fill_rect(0, 0, 10, 10, HALF_BLACK)
        self.assertEqual(img.get().getpixel((5, 5)), BLENDED)

    def test_fill_ellipse_blends_with_what_is_underneath(self):
        img = _blank()
        img.fill_ellipse(0, 0, 10, 10, HALF_BLACK)
        self.assertEqual(img.get().getpixel((5, 5)), BLENDED)

    def test_draw_rect_outline_blends(self):
        img = _blank()
        img.draw_rect(0, 0, 10, 10, HALF_BLACK, width=1)
        self.assertEqual(img.get().getpixel((0, 0)), BLENDED)
        self.assertEqual(img.get().getpixel((5, 5)), LIGHT, "outline must not fill the interior")

    def test_the_surface_stays_opaque_after_blending(self):
        img = _blank()
        img.fill_rect(0, 0, 10, 10, HALF_BLACK)
        self.assertEqual(img.get().getpixel((5, 5))[3], 255)

    def test_stacked_translucent_fills_darken_progressively(self):
        img = _blank()
        img.fill_rect(0, 0, 10, 10, (0, 0, 0, 100))
        once = img.get().getpixel((5, 5))
        img.fill_rect(0, 0, 10, 10, (0, 0, 0, 100))
        twice = img.get().getpixel((5, 5))
        self.assertLess(twice[0], once[0])
        self.assertGreater(twice[0], 0, "two passes must not saturate to black")


class TestOpaqueFillsAreUnchanged(unittest.TestCase):
    def test_opaque_fill_replaces_outright(self):
        img = _blank()
        img.fill_rect(0, 0, 10, 10, (10, 20, 30, 255))
        self.assertEqual(img.get().getpixel((5, 5)), (10, 20, 30, 255))

    def test_opaque_ellipse_replaces_outright(self):
        img = _blank()
        img.fill_ellipse(0, 0, 10, 10, (10, 20, 30, 255))
        self.assertEqual(img.get().getpixel((5, 5)), (10, 20, 30, 255))

    def test_three_channel_colour_is_treated_as_opaque(self):
        img = _blank()
        img.fill_rect(0, 0, 10, 10, (10, 20, 30))
        self.assertEqual(img.get().getpixel((5, 5))[:3], (10, 20, 30))


class TestBounds(unittest.TestCase):
    def test_blended_shape_clipped_at_the_origin(self):
        img = _blank()
        img.fill_rect(-3, -3, 6, 6, HALF_BLACK)
        self.assertEqual(img.get().getpixel((0, 0)), BLENDED)
        self.assertEqual(img.get().getpixel((9, 9)), LIGHT)

    def test_blended_shape_clipped_at_the_far_edge(self):
        img = _blank()
        img.fill_rect(7, 7, 6, 6, HALF_BLACK)
        self.assertEqual(img.get().getpixel((9, 9)), BLENDED)
        self.assertEqual(img.get().getpixel((0, 0)), LIGHT)

    def test_shape_entirely_outside_is_a_no_op(self):
        img = _blank()
        img.fill_rect(50, 50, 6, 6, HALF_BLACK)
        self.assertEqual(img.get().getpixel((5, 5)), LIGHT)

    def test_zero_and_negative_sizes_are_ignored(self):
        img = _blank()
        for w, h in ((0, 5), (5, 0), (-4, 5)):
            img.fill_rect(0, 0, w, h, HALF_BLACK)
        self.assertEqual(img.get().getpixel((5, 5)), LIGHT)


if __name__ == "__main__":
    unittest.main()
