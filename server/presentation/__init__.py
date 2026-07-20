"""Presentation layer for the multiplayer server.

Owns: the transport itself — the WebSocket lifecycle, the auth retry loop,
frame parsing, socket-bound sessions, and the JSON encoding on the way out.
Must not own: game rules, use-case orchestration, or persistence. The wire
*contract* (frame names, builders, coordinate mapping) is pure data shared with
the layers below, so it lives in server/application/dtos; this layer is only
what carries it.
"""
