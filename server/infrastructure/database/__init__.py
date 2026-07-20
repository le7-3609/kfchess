"""Persistence adapters.

Owns: the SQLite connection lifecycle and the user table it backs.
Must not own: game rules, sessions, or network routing.
"""

from server.infrastructure.database.database import Database

__all__ = ["Database"]
