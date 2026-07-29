"""Metric primitives and a Prometheus text-format registry.

Owns: counter/gauge/histogram value storage and the exposition rendering a
scraper reads.
Must not own: the meaning of any particular metric, the HTTP route that serves
it (presentation owns that), or any scheduling — a gauge that tracks a live
object is given a callable and read at scrape time.

Written against no client library on purpose. The exposition format is a dozen
lines of text, and the alternative is a runtime dependency on the critical path
of a game server for something this small.
"""

import math
import threading
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

# Prometheus' own default latency buckets are tuned for HTTP requests. These are
# tuned for a 20 Hz simulation tick: the budget is 50 ms, so the buckets cluster
# below it and then fan out, making "how far past budget" readable rather than
# collapsing every overrun into one bucket.
DEFAULT_TICK_BUCKETS_SECONDS: Tuple[float, ...] = (
    0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 1.0,
)

_TYPE_COUNTER = "counter"
_TYPE_GAUGE = "gauge"
_TYPE_HISTOGRAM = "histogram"

_POSITIVE_INFINITY_LABEL = "+Inf"


def _format_value(value: float) -> str:
    """Render a sample the way the exposition format expects.

    Integral floats print without a trailing `.0` purely for readability of a
    scrape; `+Inf`/`NaN` have their own spellings and would otherwise render as
    Python's `inf`, which a scraper rejects.
    """
    if math.isinf(value):
        return _POSITIVE_INFINITY_LABEL if value > 0 else "-Inf"
    if math.isnan(value):
        return "NaN"
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))


class Metric:
    """Common naming/help metadata every exported metric carries."""

    def __init__(self, name: str, documentation: str, metric_type: str) -> None:
        self.name = name
        self.documentation = documentation
        self.metric_type = metric_type

    def header_lines(self) -> List[str]:
        return [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} {self.metric_type}",
        ]

    def sample_lines(self) -> List[str]:
        raise NotImplementedError


class Counter(Metric):
    """A value that only ever increases, e.g. failures since process start."""

    def __init__(self, name: str, documentation: str) -> None:
        super().__init__(name, documentation, _TYPE_COUNTER)
        self._value = 0.0
        self._lock = threading.Lock()

    @property
    def value(self) -> float:
        return self._value

    def increment(self, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("A counter may not decrease")
        with self._lock:
            self._value += amount

    def sample_lines(self) -> List[str]:
        return [f"{self.name} {_format_value(self._value)}"]


class Gauge(Metric):
    """A value that moves in both directions, e.g. rooms currently active.

    Backed either by a stored number or by a *source* callable read at scrape
    time. The callable form is what lets a live object (a room registry, a
    queue) be exported without anything having to push updates on every change.
    """

    def __init__(
        self,
        name: str,
        documentation: str,
        source: Optional[Callable[[], float]] = None,
    ) -> None:
        super().__init__(name, documentation, _TYPE_GAUGE)
        self._source = source
        self._value = 0.0

    @property
    def value(self) -> float:
        return float(self._source()) if self._source is not None else self._value

    def set(self, value: float) -> None:
        if self._source is not None:
            raise ValueError(f"Gauge {self.name!r} is source-backed and cannot be set directly")
        self._value = float(value)

    def sample_lines(self) -> List[str]:
        try:
            current = self.value
        except Exception:
            # A scrape must never fail because one live object it reads through
            # is mid-teardown; an absent sample is a gap in a graph, an
            # exception here is a blind spot across every metric in the process.
            return []
        return [f"{self.name} {_format_value(current)}"]


class Histogram(Metric):
    """Cumulative bucket counts plus sum and count, for latency distributions."""

    def __init__(
        self,
        name: str,
        documentation: str,
        buckets: Sequence[float] = DEFAULT_TICK_BUCKETS_SECONDS,
    ) -> None:
        super().__init__(name, documentation, _TYPE_HISTOGRAM)
        self._bounds = tuple(sorted(buckets))
        self._counts = [0 for _ in self._bounds]
        self._sum = 0.0
        self._count = 0
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        return self._count

    @property
    def sum(self) -> float:
        return self._sum

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            for index, bound in enumerate(self._bounds):
                if value <= bound:
                    self._counts[index] += 1

    def sample_lines(self) -> List[str]:
        lines: List[str] = []
        cumulative = 0
        for index, bound in enumerate(self._bounds):
            cumulative = self._counts[index]
            lines.append(f'{self.name}_bucket{{le="{_format_value(bound)}"}} {cumulative}')
        lines.append(f'{self.name}_bucket{{le="{_POSITIVE_INFINITY_LABEL}"}} {self._count}')
        lines.append(f"{self.name}_sum {_format_value(self._sum)}")
        lines.append(f"{self.name}_count {self._count}")
        return lines


class MetricsRegistry:
    """Holds every exported metric and renders a scrape response."""

    def __init__(self) -> None:
        self._metrics: Dict[str, Metric] = {}

    def register(self, metric: Metric) -> Metric:
        """Add *metric*, or return the existing one registered under its name.

        Re-registration returns the incumbent rather than raising: the same
        process may build two servers (a test that constructs several), and a
        duplicate metric name is a wiring detail, not a reason to fail startup.
        """
        existing = self._metrics.get(metric.name)
        if existing is not None:
            return existing
        self._metrics[metric.name] = metric
        return metric

    def counter(self, name: str, documentation: str) -> Counter:
        return self.register(Counter(name, documentation))

    def gauge(
        self, name: str, documentation: str, source: Optional[Callable[[], float]] = None
    ) -> Gauge:
        return self.register(Gauge(name, documentation, source))

    def histogram(
        self,
        name: str,
        documentation: str,
        buckets: Sequence[float] = DEFAULT_TICK_BUCKETS_SECONDS,
    ) -> Histogram:
        return self.register(Histogram(name, documentation, buckets))

    def get(self, name: str) -> Optional[Metric]:
        return self._metrics.get(name)

    def unregister(self, name: str) -> None:
        """Drop a metric, so a torn-down server stops being scraped through it."""
        self._metrics.pop(name, None)

    def metrics(self) -> Iterable[Metric]:
        return list(self._metrics.values())

    def render(self) -> str:
        """Render every metric in Prometheus text exposition format."""
        lines: List[str] = []
        for metric in sorted(self._metrics.values(), key=lambda m: m.name):
            samples = metric.sample_lines()
            if not samples:
                continue
            lines.extend(metric.header_lines())
            lines.extend(samples)
        return "\n".join(lines) + "\n" if lines else ""


_DEFAULT_REGISTRY = MetricsRegistry()


def default_registry() -> MetricsRegistry:
    """The process-wide registry.

    A module-level default rather than an injected one, because the objects
    worth measuring (a room's tick loop) sit several constructor hops below the
    composition root, and threading a registry through every one of them would
    put observability plumbing into signatures that have nothing to do with it.
    """
    return _DEFAULT_REGISTRY
