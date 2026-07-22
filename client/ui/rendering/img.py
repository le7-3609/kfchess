"""Img — pixel-buffer/drawing primitive backed by Pillow (Layer 6).

Owns: composing and drawing pixels into an in-memory RGBA image.
Must not own: game rules, board mutation, input parsing, or timing.

Every pixel PillowRenderer puts on screen goes through this class.

Translucent colors are composited by hand. ImageDraw's own "RGBA" blend mode
only applies when the target is an RGB image; against an RGBA target like ours
it writes the color straight in, alpha and all, replacing whatever was beneath
instead of blending with it. So a translucent fill is drawn onto a transparent
layer and alpha-composited down. Opaque colors skip that and draw direct —
blending them would cost the same and mean nothing.
"""

from PIL import Image, ImageDraw, ImageFont

from client.ui.consts import COLOR_CHANNEL_MAX, IMAGE_MODE_RGBA, SPRITE_TRANSPARENT_RGBA

_FONT_CACHE: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

_TRANSPARENT = SPRITE_TRANSPARENT_RGBA

_DEFAULT_FONT_FILE = "arial.ttf"
# An (r, g, b, a) tuple; anything shorter carries no alpha channel.
_RGBA_CHANNEL_COUNT = 4
_ALPHA_CHANNEL_INDEX = 3
# Pillow text anchor: left-ascender (top-left of the text box).
_TEXT_ANCHOR_LEFT_ASCENDER = "la"


def _is_opaque(color: tuple) -> bool:
    return len(color) < _RGBA_CHANNEL_COUNT or color[_ALPHA_CHANNEL_INDEX] >= COLOR_CHANNEL_MAX


def _get_font(font_size: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = int(font_size)
    font = _FONT_CACHE.get(size)
    if font is None:
        try:
            font = ImageFont.truetype(_DEFAULT_FONT_FILE, size)
        except OSError:
            font = ImageFont.load_default()
        _FONT_CACHE[size] = font
    return font


class Img:
    """Lightweight image-utility class using only Pillow as the pixel-buffer/draw backend."""

    def __init__(self):
        self._img: Image.Image | None = None

    def blank(self, width: int, height: int, color: tuple[int, int, int, int] = _TRANSPARENT) -> "Img":
        self._img = Image.new(IMAGE_MODE_RGBA, (width, height), color)
        return self

    def from_pil(self, image: Image.Image) -> "Img":
        self._img = image.convert(IMAGE_MODE_RGBA)
        return self

    def copy(self) -> "Img":
        """An independent copy, sharing no pixels with this image."""
        self._require_loaded()
        clone = Img()
        clone._img = self._img.copy()
        return clone

    def read(self, path: str, target_size: tuple[int, int] | None = None, keep_aspect: bool = False) -> "Img":
        try:
            img = Image.open(path)
        except (OSError, FileNotFoundError) as e:
            raise ValueError(f"Cannot load image: {path}") from e
        img = img.convert(IMAGE_MODE_RGBA)

        if target_size is not None:
            tw, th = target_size
            w, h = img.size
            if keep_aspect:
                scale = min(tw / w, th / h)
                nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
            else:
                nw, nh = tw, th
            img = img.resize((nw, nh), Image.BILINEAR)

        self._img = img
        return self

    def draw_on(self, other: "Img", x: int, y: int) -> None:
        if self._img is None or other._img is None:
            raise ValueError("Both images must be loaded.")
        other._img.alpha_composite(self._img, dest=(x, y))

    def fill_rect(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int, int]) -> None:
        self._draw_shape(x, y, w, h, color, lambda draw, box: draw.rectangle(box, fill=color))

    def draw_rect(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int, int], width: int = 1) -> None:
        self._draw_shape(
            x, y, w, h, color, lambda draw, box: draw.rectangle(box, outline=color, width=width)
        )

    def fill_ellipse(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int, int]) -> None:
        self._draw_shape(x, y, w, h, color, lambda draw, box: draw.ellipse(box, fill=color))

    def draw_line(self, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int, int], width: int = 1) -> None:
        self._require_loaded()
        self._draw_unbounded(color, lambda draw: draw.line([x1, y1, x2, y2], fill=color, width=width))

    def put_text(
        self,
        text: str,
        x: int,
        y: int,
        font_size: float,
        color: tuple[int, int, int, int],
        anchor: str = _TEXT_ANCHOR_LEFT_ASCENDER,
    ) -> None:
        self._require_loaded()
        font = _get_font(font_size)
        self._draw_unbounded(
            color, lambda draw: draw.text((x, y), text, fill=color, font=font, anchor=anchor)
        )

    def _draw_shape(self, x: int, y: int, w: int, h: int, color: tuple, paint) -> None:
        """Paint a w-by-h shape at (x, y), blending it if *color* is translucent.

        The blend layer is only the size of the shape, not of the whole image:
        these run per-piece on every frame, so a full-image composite each time
        would be paid 30-odd times a frame for a few cells' worth of pixels.
        """
        if w <= 0 or h <= 0:
            return
        self._require_loaded()
        if _is_opaque(color):
            paint(ImageDraw.Draw(self._img), [x, y, x + w - 1, y + h - 1])
            return
        layer = Image.new(IMAGE_MODE_RGBA, (w, h), _TRANSPARENT)
        paint(ImageDraw.Draw(layer), [0, 0, w - 1, h - 1])
        self._composite(layer, x, y)

    def _draw_unbounded(self, color: tuple, paint) -> None:
        """Blend a shape whose extent we cannot cheaply predict (text, lines).

        Falls back to a full-image layer, which only translucent draws pay for.
        """
        if _is_opaque(color):
            paint(ImageDraw.Draw(self._img))
            return
        layer = Image.new(IMAGE_MODE_RGBA, self._img.size, _TRANSPARENT)
        paint(ImageDraw.Draw(layer))
        self._composite(layer, 0, 0)

    def _composite(self, layer: Image.Image, x: int, y: int) -> None:
        """Alpha-composite *layer* onto the image at (x, y), clipping at the edges."""
        region = self._img.crop((x, y, x + layer.width, y + layer.height))
        self._img.paste(Image.alpha_composite(region, layer), (x, y))

    def resize(self, w: int, h: int) -> "Img":
        self._require_loaded()
        self._img = self._img.resize((w, h), Image.BILINEAR)
        return self

    def show(self) -> None:
        self._require_loaded()
        self._img.show()

    def get(self) -> Image.Image:
        self._require_loaded()
        return self._img

    def _require_loaded(self) -> None:
        if self._img is None:
            raise ValueError("Image not loaded.")
