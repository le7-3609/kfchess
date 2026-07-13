"""Image view interface — pixel output sink (Layer 6).

Owns: displaying an already-rendered image.
Must not own: game rules, board mutation, input parsing, or text-test logic.
"""

from abc import ABC, abstractmethod


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
