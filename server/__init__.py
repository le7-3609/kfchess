"""Multiplayer server layer for Kung Fu Chess.

Owns: WebSocket networking, authentication, matchmaking, room management,
and disconnect handling.
Must not own: game rules, board mutation, rendering, or timing.
"""
