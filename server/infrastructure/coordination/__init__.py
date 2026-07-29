"""Infrastructure layer — drivers for the state the fleet shares.

Owns: the Redis connection, the key layout every replica agrees on, and the
scripts that make a multi-key change atomic.
Must not own: any policy about what the shared state means. Pairing rules live
in `server/domain/matchmaking`, room lifecycle in `server/application`; the
modules here move bytes and guarantee atomicity, nothing more.
"""
