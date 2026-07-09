"""Image view — displays a rendered image of the board.

Owns: presenting images produced by a Renderer to a UI window.
Must not own: game rules, Board mutation, input parsing, or text-test logic.

This is a stub; a real implementation would use a GUI framework (e.g. Pygame,
Tkinter, or PIL) to open a window and blit rendered frames.
"""

from abc import ABC, abstractmethod
from typing import Optional


class ImageViewInterface(ABC):
    """Abstract image view — presents rendered frames."""

    @abstractmethod
    def show(self, image: object) -> None:
        """Display *image* in the UI window."""


class NullImageView(ImageViewInterface):
    """No-op image view for headless / text-test use."""

    def show(self, image: object) -> None:
        pass  # Nothing to show in headless mode.
