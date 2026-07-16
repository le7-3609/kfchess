"""Registry of selectable board color themes (Layer 2).

Owns: the fixed list of (id, display name, light/dark square color) triples
for the board color palettes. Must not own: painting the checkerboard
(PillowRenderer) or persisting the player's choice (user_settings_store).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BoardTheme:
    theme_id: str
    display_name: str
    light_color: tuple[int, int, int, int]
    dark_color: tuple[int, int, int, int]


BOARD_THEMES: list[BoardTheme] = [
    BoardTheme("classic", "Classic", (240, 217, 181, 255), (181, 136, 99, 255)),
    BoardTheme("green", "Green", (238, 238, 210, 255), (118, 150, 86, 255)),
    BoardTheme("blue", "Blue", (234, 240, 246, 255), (75, 115, 153, 255)),
    BoardTheme("dark", "Dark", (120, 120, 120, 255), (40, 40, 40, 255)),
]

DEFAULT_THEME_ID = "classic"

_BY_ID = {theme.theme_id: theme for theme in BOARD_THEMES}


def get_theme(theme_id: str) -> BoardTheme:
    return _BY_ID.get(theme_id, _BY_ID[DEFAULT_THEME_ID])
