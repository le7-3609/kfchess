"""Domain layer for the multiplayer server.

Owns: pure entities and value objects for sessions, matchmaking, and game
rooms — identity, invariants, and lifecycle state.
Must not own: WebSocket transport, JSON wire encoding, or database
persistence. Those live in the server package's infrastructure-facing
modules, which compose these entities rather than duplicate their state.
"""
