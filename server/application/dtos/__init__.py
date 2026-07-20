"""Application DTOs — the data shapes the server exchanges with clients.

Owns: the wire contract (frame vocabulary, outbound frame builders, and the
domain <-> wire coordinate/snapshot mapping) plus the identity and error types
crossing the presentation boundary.

These live in the application layer, not presentation, because they are pure
data with no I/O: a builder returns a dict and a mapper returns a Position.
Both the use cases and the socket machinery need them, and the shared
dependency belongs in the inner layer so the arrows keep pointing one way.
Actually writing bytes — JSON encoding, socket sends, the WebSocket lifecycle —
stays in server/presentation.
"""

from server.application.dtos.common import ERROR_ALREADY_SEATED, Identity

__all__ = ["Identity", "ERROR_ALREADY_SEATED"]
