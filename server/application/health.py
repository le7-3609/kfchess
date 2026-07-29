"""Liveness and readiness policy — what "this process can do useful work" means.

Layer: application (server/application)
Owns: the named dependency checks a replica must satisfy before traffic is sent
to it, and the drain flag that withdraws a replica from rotation without
declaring it dead.
Must not own: HTTP status codes or route shapes (presentation formats the
report), nor the checks themselves — each collaborator supplies its own probe.

The split between the two probes is deliberate and load-bearing for Step 8's
Kubernetes probes:

* **Liveness** is purely local — the process exists and its event loop still
  turns. It touches nothing external, because a liveness check that reaches the
  database turns one slow database into a fleet-wide restart storm: every
  replica fails its probe simultaneously and the restarts hit the struggling
  database harder.
* **Readiness** is "I can serve a request right now", so it *does* consult
  external dependencies. A replica that fails it stops receiving new work while
  its existing rooms keep running.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, Mapping

_LOGGER = logging.getLogger(__name__)

# A single check may not hold the probe open indefinitely: a readiness endpoint
# that hangs is indistinguishable from a dead replica to most probers, and gets
# the replica killed rather than merely drained.
DEFAULT_CHECK_TIMEOUT_SECONDS = 2.0

ReadinessCheck = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class ReadinessReport:
    """The outcome of one readiness evaluation, per named dependency."""

    ready: bool
    draining: bool
    checks: Mapping[str, bool]


class ReadinessProbe:
    """Aggregates named dependency checks into one ready/not-ready answer."""

    def __init__(self, check_timeout_seconds: float = DEFAULT_CHECK_TIMEOUT_SECONDS) -> None:
        self._checks: Dict[str, ReadinessCheck] = {}
        self._check_timeout_seconds = check_timeout_seconds
        self._draining = False

    @property
    def draining(self) -> bool:
        return self._draining

    def register(self, name: str, check: ReadinessCheck) -> None:
        """Add a dependency whose health gates this replica's readiness."""
        self._checks[name] = check

    def begin_draining(self) -> None:
        """Report not-ready from now on while staying alive.

        This is how a replica leaves the load balancer's rotation on SIGTERM
        without having its in-flight games killed: readiness fails, liveness
        keeps passing, and the games already running are allowed to finish.
        """
        self._draining = True

    async def evaluate(self) -> ReadinessReport:
        """Run every registered check and summarize the result.

        A check that raises or times out counts as failed rather than
        propagating: the probe's job is to answer, and an exception escaping
        here would turn a degraded dependency into a 500 that reads as a broken
        replica.
        """
        results = {
            name: await self._run_check(name, check) for name, check in self._checks.items()
        }
        ready = not self._draining and all(results.values())
        return ReadinessReport(ready=ready, draining=self._draining, checks=results)

    async def _run_check(self, name: str, check: ReadinessCheck) -> bool:
        try:
            return bool(await asyncio.wait_for(check(), timeout=self._check_timeout_seconds))
        except asyncio.TimeoutError:
            _LOGGER.warning("Readiness check %r timed out", name)
            return False
        except Exception as exc:
            _LOGGER.warning("Readiness check %r failed: %s", name, exc)
            return False
