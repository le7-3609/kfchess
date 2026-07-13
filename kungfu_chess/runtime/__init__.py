"""Runtime layer — drives the simulation clock (Layer 9).

Owns: deciding *when* GameEngine._resolve_pending()-equivalent advancement
happens (synchronous "wait N" for tests, or a real asyncio tick loop for
production/websocket play) and queuing concurrently-submitted commands so
they're applied once per tick rather than interleaved mid-resolve.

Must not own: chess legality, movement rules, Board mutation semantics
(all of that still lives in GameEngine/RealTimeArbiter) — this layer only
calls the engine's existing public command API on a schedule.
"""

from kungfu_chess.runtime.async_runner import AsyncGameRunner

__all__ = ["AsyncGameRunner"]
