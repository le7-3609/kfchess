"""Application layer for the multiplayer server.

Owns: use-case orchestration — the sequences that turn a client's intent into
domain state changes and multi-party notifications (authenticate, queue, seat,
move, reconnect, tear down).
Must not own: wire framing or socket lifecycle (presentation), pairing and seat
invariants (domain), or persistence mechanics (infrastructure).

Use cases return `Result` rather than sending error frames: the requester's
rejection is the presentation layer's to phrase and deliver. Notifications that
fan out to *other* participants are sent here, since only the use case knows
who else the outcome concerns.
"""
