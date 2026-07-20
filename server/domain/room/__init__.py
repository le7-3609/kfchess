"""Room subdomain.

Owns: room lifecycle state, seat bindings, and move authorization.
Must not own: network transport, event broadcasting, or persistence.
"""
