from abc import ABC, abstractmethod


class ImageViewInterface(ABC):

    @abstractmethod
    def show(self, image: object) -> None:
        pass


class NullImageView(ImageViewInterface):

    def show(self, image: object) -> None:
        pass
