"""Image view interface — pixel output sink (Layer 6).

Owns: displaying an already-rendered image.
Must not own: game rules, board mutation, input parsing, or text-test logic.
"""

import tkinter as tk
from abc import ABC, abstractmethod

from PIL import ImageTk


class ImageViewInterface(ABC):
    """Displays a rendered image.

    Sits below RendererInterface in the drawing pipeline: a Renderer reads a
    GameSnapshot and paints an image, then hands that image here to be
    shown. Like RendererInterface, this never touches GameState or
    BoardInterface — it only ever sees the finished, rendered output.
    """

    @abstractmethod
    def show(self, image: object) -> None:
        pass


class NullImageView(ImageViewInterface):

    def show(self, image: object) -> None:
        pass


class TkImageView(ImageViewInterface):
    """Displays an already-rendered Img on a tkinter Canvas."""

    def __init__(self, canvas: tk.Canvas, canvas_image_id: int):
        self._canvas = canvas
        self._canvas_image_id = canvas_image_id
        self._tk_image = None  # keep a reference alive; tkinter drops GC'd images

    def show(self, image: object) -> None:
        self._tk_image = ImageTk.PhotoImage(image.get())
        self._canvas.itemconfig(self._canvas_image_id, image=self._tk_image)
