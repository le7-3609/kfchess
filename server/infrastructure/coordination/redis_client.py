"""Redis connection — one pool per process, shared by every Redis-backed store.

Owns: the connection pool's lifecycle, the key prefix every store namespaces
under, the readiness ping, and the registration of Lua scripts.
Must not own: key layout for any particular store (each store names its own
keys), or the meaning of anything stored.

`redis` is an optional import. A process with no Redis configured must still
start, run, and pass its tests — the in-process stores behind the same ports are
the default, not a fallback — so a missing driver is only an error at the moment
someone actually asks for a Redis-backed store.
"""

import logging
from typing import Any, List, Optional

try:
    import redis.asyncio as aioredis
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - exercised only where redis is absent
    aioredis = None  # type: ignore[assignment]

    class RedisError(Exception):  # type: ignore[no-redef]
        """Stand-in so callers can name the driver's error type unconditionally."""


_LOGGER = logging.getLogger(__name__)

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_KEY_PREFIX = "kfchess"

# Bounds how long a store waits on a Redis that has gone away. Readiness has its
# own 2s budget in ReadinessProbe, so anything slower than this is already a
# replica that should be drained rather than one worth waiting for.
DEFAULT_SOCKET_TIMEOUT_SECONDS = 2.0


def redis_available() -> bool:
    """Whether the driver is importable in this process."""
    return aioredis is not None


class RedisConnection:
    """A lazily-opened Redis pool plus the script cache the stores share."""

    def __init__(
        self,
        url: str = DEFAULT_REDIS_URL,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        socket_timeout_seconds: float = DEFAULT_SOCKET_TIMEOUT_SECONDS,
    ) -> None:
        if aioredis is None:
            raise RuntimeError(
                "The 'redis' package is required for Redis-backed coordination; "
                "install it or leave KFCHESS_REDIS_URL unset to run in-process."
            )
        self._url = url
        self._key_prefix = key_prefix
        self._client = aioredis.from_url(
            url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=socket_timeout_seconds,
            socket_connect_timeout=socket_timeout_seconds,
        )

    @property
    def client(self) -> Any:
        return self._client

    def key(self, *parts: str) -> str:
        """Namespace a key so several deployments can share one Redis."""
        return ":".join((self._key_prefix,) + parts)

    def script(self, body: str) -> Any:
        """Register a Lua script, returning a callable that runs it.

        redis-py caches the SHA and falls back to a full EVAL after a Redis
        restart flushes the script cache, so a redeploy of Redis does not break
        the callers holding these handles.
        """
        return self._client.register_script(body)

    async def ping(self) -> bool:
        """Whether this replica can still reach Redis, for the readiness probe."""
        try:
            return bool(await self._client.ping())
        except (RedisError, OSError) as exc:
            _LOGGER.warning("Redis ping failed: %s", exc)
            return False

    async def close(self) -> None:
        await self._client.aclose()


async def scripted(script: Any, keys: List[str], args: List[Any]) -> Optional[Any]:
    """Run a registered script, containing a driver failure as None.

    Every caller here is on a path where "Redis did not answer" must read as
    "nothing happened this cycle" rather than as an exception unwinding a
    matchmaking sweep or a lease renewal loop.
    """
    try:
        return await script(keys=keys, args=args)
    except (RedisError, OSError) as exc:
        _LOGGER.warning("Redis script failed: %s", exc)
        return None
