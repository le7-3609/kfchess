"""Img — pixel-buffer/drawing primitive backed by Pillow (Layer 6).

Owns: composing and drawing pixels into an in-memory RGBA image.
Must not own: game rules, board mutation, input parsing, or timing.

Every pixel PillowRenderer puts on screen goes through this class.
"""

from PIL import Image, ImageDraw, ImageFont

_FONT_CACHE: dict[int, ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _get_font(font_size: float) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = int(font_size)
    font = _FONT_CACHE.get(size)
    if font is None:
        try:
            font = ImageFont.truetype("arial.ttf", size)
        except OSError:
            font = ImageFont.load_default()
        _FONT_CACHE[size] = font
    return font


class Img:
    """Lightweight image-utility class using only Pillow as the pixel-buffer/draw backend."""

    def __init__(self):
        self._img: Image.Image | None = None

    # -- construction -----------------------------------------------------

    def blank(self, width: int, height: int, color: tuple[int, int, int, int] = (0, 0, 0, 0)) -> "Img":
        self._img = Image.new("RGBA", (width, height), color)
        return self

    def from_pil(self, image: Image.Image) -> "Img":
        self._img = image.convert("RGBA")
        return self

    def read(self, path: str, target_size: tuple[int, int] | None = None, keep_aspect: bool = False) -> "Img":
        try:
            img = Image.open(path)
        except (OSError, FileNotFoundError) as e:
            raise ValueError(f"Cannot load image: {path}") from e
        img = img.convert("RGBA")

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

    # -- compositing --------------------------------------------------------

    def draw_on(self, other: "Img", x: int, y: int) -> None:
        if self._img is None or other._img is None:
            raise ValueError("Both images must be loaded.")
        other._img.alpha_composite(self._img, dest=(x, y))

    def fill_rect(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int, int]) -> None:
        if w <= 0 or h <= 0:
            return
        self._require_loaded()
        draw = ImageDraw.Draw(self._img, "RGBA")
        draw.rectangle([x, y, x + w - 1, y + h - 1], fill=color)

    def draw_rect(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int, int], width: int = 1) -> None:
        if w <= 0 or h <= 0:
            return
        self._require_loaded()
        draw = ImageDraw.Draw(self._img, "RGBA")
        draw.rectangle([x, y, x + w - 1, y + h - 1], outline=color, width=width)

    def fill_ellipse(self, x: int, y: int, w: int, h: int, color: tuple[int, int, int, int]) -> None:
        if w <= 0 or h <= 0:
            return
        self._require_loaded()
        draw = ImageDraw.Draw(self._img, "RGBA")
        draw.ellipse([x, y, x + w - 1, y + h - 1], fill=color)

    def draw_line(self, x1: int, y1: int, x2: int, y2: int, color: tuple[int, int, int, int], width: int = 1) -> None:
        self._require_loaded()
        draw = ImageDraw.Draw(self._img, "RGBA")
        draw.line([x1, y1, x2, y2], fill=color, width=width)

    def put_text(
        self,
        text: str,
        x: int,
        y: int,
        font_size: float,
        color: tuple[int, int, int, int],
        anchor: str = "la",
    ) -> None:
        self._require_loaded()
        draw = ImageDraw.Draw(self._img, "RGBA")
        font = _get_font(font_size)
        draw.text((x, y), text, fill=color, font=font, anchor=anchor)

    def resize(self, w: int, h: int) -> "Img":
        self._require_loaded()
        self._img = self._img.resize((w, h), Image.BILINEAR)
        return self

    # -- output -------------------------------------------------------------

    def show(self) -> None:
        self._require_loaded()
        self._img.show()

    def get(self) -> Image.Image:
        self._require_loaded()
        return self._img

    def _require_loaded(self) -> None:
        if self._img is None:
            raise ValueError("Image not loaded.")
