"""Player subdomain.

Owns: the seat contract a room talks to, and the automated player.
Must not own: sockets or wire encoding.
"""

from server.domain.player.player_interface import (
    DEFAULT_BOT_ELO,
    DEFAULT_BOT_USERNAME,
    BotPlayerAdapter,
    PlayerInterface,
)

__all__ = [
    "PlayerInterface",
    "BotPlayerAdapter",
    "DEFAULT_BOT_USERNAME",
    "DEFAULT_BOT_ELO",
]
