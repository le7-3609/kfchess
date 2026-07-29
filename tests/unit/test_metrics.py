"""Unit tests for the metric primitives and their Prometheus rendering.

A metrics pipeline that lies produces a fleet that scales wrongly, so the
exposition format itself is asserted here rather than assumed.
"""

import pytest

from server.infrastructure.observability.metrics import (
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
)
from server.infrastructure.observability.server_metrics import (
    METRIC_ROOMS_ACTIVE,
    METRIC_TICK_DURATION,
    ServerMetrics,
)


def test_counter_accumulates_and_renders_as_a_counter():
    counter = Counter("kfchess_test_total", "A test counter.")
    counter.increment()
    counter.increment(3)

    assert counter.value == 4
    assert counter.sample_lines() == ["kfchess_test_total 4"]
    assert "# TYPE kfchess_test_total counter" in counter.header_lines()


def test_counter_refuses_to_decrease():
    """A counter that can go down is not a counter, and every rate() over it
    would report a spike where the decrease happened."""
    counter = Counter("kfchess_test_total", "A test counter.")

    with pytest.raises(ValueError):
        counter.increment(-1)


def test_source_backed_gauge_reads_the_live_object_at_scrape_time():
    rooms = ["A", "B"]
    gauge = Gauge(METRIC_ROOMS_ACTIVE, "Rooms.", source=lambda: len(rooms))

    assert gauge.value == 2
    rooms.pop()
    assert gauge.value == 1


def test_source_backed_gauge_cannot_also_be_set_directly():
    """Two sources of truth for one number can only ever disagree."""
    gauge = Gauge("kfchess_test", "Test.", source=lambda: 1)

    with pytest.raises(ValueError):
        gauge.set(5)


def test_a_gauge_whose_source_raises_is_omitted_rather_than_failing_the_scrape():
    """One live object mid-teardown must not blind every other metric in the
    process."""

    def broken():
        raise RuntimeError("object is gone")

    registry = MetricsRegistry()
    registry.gauge("kfchess_broken", "Broken.", broken)
    registry.counter("kfchess_fine_total", "Fine.").increment()

    rendered = registry.render()

    assert "kfchess_broken" not in rendered
    assert "kfchess_fine_total 1" in rendered


def test_histogram_buckets_are_cumulative_with_sum_and_count():
    histogram = Histogram("kfchess_tick_seconds", "Ticks.", buckets=(0.01, 0.05, 0.1))
    for observed in (0.005, 0.02, 0.2):
        histogram.observe(observed)

    lines = histogram.sample_lines()

    assert 'kfchess_tick_seconds_bucket{le="0.01"} 1' in lines
    assert 'kfchess_tick_seconds_bucket{le="0.05"} 2' in lines
    assert 'kfchess_tick_seconds_bucket{le="0.1"} 2' in lines
    assert 'kfchess_tick_seconds_bucket{le="+Inf"} 3' in lines
    assert "kfchess_tick_seconds_count 3" in lines
    assert histogram.sum == pytest.approx(0.225)


def test_registry_renders_help_and_type_headers_for_each_metric():
    registry = MetricsRegistry()
    registry.counter("kfchess_a_total", "First.").increment()
    registry.gauge("kfchess_b", "Second.", source=lambda: 7)

    rendered = registry.render()

    assert "# HELP kfchess_a_total First." in rendered
    assert "# TYPE kfchess_a_total counter" in rendered
    assert "# HELP kfchess_b Second." in rendered
    assert "# TYPE kfchess_b gauge" in rendered
    assert "kfchess_b 7" in rendered
    assert rendered.endswith("\n")


def test_registering_the_same_name_twice_returns_the_incumbent():
    """Two servers in one process is a wiring detail, not a reason to fail
    startup — but they must share one series, not silently shadow each other."""
    registry = MetricsRegistry()
    first = registry.counter("kfchess_dup_total", "First.")
    second = registry.counter("kfchess_dup_total", "Second.")

    first.increment()

    assert first is second
    assert second.value == 1


def test_server_metrics_exports_zeroed_counters_before_anything_happens():
    """A missing series and a series pinned at zero look identical on a graph
    and mean opposite things."""
    registry = MetricsRegistry()
    ServerMetrics(registry)

    rendered = registry.render()

    assert "kfchess_broadcast_failures_total 0" in rendered
    assert METRIC_TICK_DURATION in rendered


def test_unbinding_live_gauges_stops_a_stopped_server_being_scraped():
    registry = MetricsRegistry()
    metrics = ServerMetrics(registry)
    metrics.bind_rooms_active(lambda: 3)
    assert f"{METRIC_ROOMS_ACTIVE} 3" in registry.render()

    metrics.unbind_live_gauges()

    assert METRIC_ROOMS_ACTIVE not in registry.render()


def test_http_metrics_are_part_of_the_catalogue():
    """The API tier's RED metrics: after the roles split there is no shared
    process for a slow login to show up in, so it needs its own series."""
    registry = MetricsRegistry()
    ServerMetrics(registry)

    rendered = registry.render()

    assert "kfchess_http_requests_total 0" in rendered
    assert "kfchess_http_errors_total 0" in rendered
    assert "kfchess_http_request_duration_seconds_count 0" in rendered
