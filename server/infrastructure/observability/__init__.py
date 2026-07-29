"""Observability infrastructure — metrics collection and exposition.

Owns: the in-process metric registry and its Prometheus text rendering.
Must not own: what any metric means, or when it is updated — callers own that.
"""
