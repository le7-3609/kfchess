"""Unit tests for the readiness probe.

The probe's contract is what Kubernetes' rollout mechanism rests on, so the
cases that matter are the awkward ones: a check that hangs, a check that
raises, and a replica that is draining but not dead.
"""

import asyncio

import pytest

from server.application.health import ReadinessProbe


@pytest.mark.asyncio
async def test_probe_with_no_checks_is_ready():
    """A process with no external dependencies is ready by definition."""
    report = await ReadinessProbe().evaluate()

    assert report.ready
    assert not report.draining
    assert report.checks == {}


@pytest.mark.asyncio
async def test_every_passing_check_makes_the_replica_ready():
    probe = ReadinessProbe()
    probe.register("database", _always(True))
    probe.register("broker", _always(True))

    report = await probe.evaluate()

    assert report.ready
    assert report.checks == {"database": True, "broker": True}


@pytest.mark.asyncio
async def test_one_failing_check_makes_the_replica_not_ready():
    """Readiness is an AND: a replica that cannot reach its database cannot
    serve, however healthy the rest of it is."""
    probe = ReadinessProbe()
    probe.register("database", _always(False))
    probe.register("broker", _always(True))

    report = await probe.evaluate()

    assert not report.ready
    assert report.checks == {"database": False, "broker": True}


@pytest.mark.asyncio
async def test_a_raising_check_counts_as_failed_rather_than_propagating():
    """The probe's job is to answer. An exception escaping would turn a
    degraded dependency into a 500 that reads as a broken replica."""

    async def explode():
        raise RuntimeError("connection refused")

    probe = ReadinessProbe()
    probe.register("database", explode)

    report = await probe.evaluate()

    assert not report.ready
    assert report.checks == {"database": False}


@pytest.mark.asyncio
async def test_a_hanging_check_times_out_rather_than_hanging_the_probe():
    """A readiness endpoint that never answers is indistinguishable from a dead
    replica to most probers, and gets the replica killed rather than drained."""

    async def never_returns():
        await asyncio.sleep(10)
        return True

    probe = ReadinessProbe(check_timeout_seconds=0.05)
    probe.register("database", never_returns)

    report = await asyncio.wait_for(probe.evaluate(), timeout=2.0)

    assert not report.ready
    assert report.checks == {"database": False}


@pytest.mark.asyncio
async def test_draining_reports_not_ready_while_every_check_still_passes():
    """Drain is how a replica leaves rotation without its live games being
    killed: not ready, but nothing is actually wrong with it."""
    probe = ReadinessProbe()
    probe.register("database", _always(True))

    probe.begin_draining()
    report = await probe.evaluate()

    assert not report.ready
    assert report.draining
    assert report.checks == {"database": True}


def _always(value: bool):
    async def check() -> bool:
        return value

    return check
