"""Infrastructure layer for the multiplayer server.

Owns: adapters to the outside world — SQLite persistence, background timing
services (disconnect countdowns, heartbeats, bot cadence), and logging sinks.
Must not own: domain invariants, use-case orchestration, or wire-frame
encoding. Infrastructure is depended upon by the layers above it and depends
on none of them.
"""
