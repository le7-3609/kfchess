"""Registry of selectable piece-sprite themes (Layer 2).

Owns: the fixed list of (id, folder name, display name) triples for the art
sets under assets/. Must not own: reading files from disk (SpriteLibrary) or
persisting the player's choice (user_settings_store).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class PieceTheme:
    theme_id: str
    folder_name: str
    display_name: str


PIECE_THEMES: list[PieceTheme] = [
    PieceTheme("pieces1", "pieces1", "Classic"),
    PieceTheme("pieces2", "pieces2", "Modern"),
    PieceTheme("pieces3", "pieces3", "Minimal"),
    PieceTheme("pieces_mine", "pieces_mine", "My Pieces"),
    PieceTheme("pieces", "pieces", "Pieces"),
]

DEFAULT_THEME_ID = "pieces2"

_BY_ID = {theme.theme_id: theme for theme in PIECE_THEMES}


def get_theme(theme_id: str) -> PieceTheme:
    return _BY_ID.get(theme_id, _BY_ID[DEFAULT_THEME_ID])
