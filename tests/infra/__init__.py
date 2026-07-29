"""Tests that need a real backing service rather than an in-process stand-in.

Everything here is marked `infra` and skips itself when the service it needs is
not configured. That is deliberate: the in-process implementations behind each
port are what keep `pytest` green with no containers, and these are what prove
the *other* implementation of the same port actually satisfies it — an
in-memory dict will agree with any contract, including a wrong one.
"""
