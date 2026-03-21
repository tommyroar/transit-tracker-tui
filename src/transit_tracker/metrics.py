"""In-process metrics collector with time-series ring buffers.

Designed for a single running instance.  Stores recent data points so
the observability dashboard can render time-series charts without an
external metrics backend.

All public functions are thread-safe.
"""

import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Ring-buffer time-series store
# ---------------------------------------------------------------------------

_MAX_POINTS = 1800  # ~30 minutes at 1-second resolution, or ~15h at 30s


class _TimeSeries:
    """Thread-safe ring buffer of (timestamp, value) pairs."""

    __slots__ = ("name", "unit", "_data", "_lock")

    def __init__(self, name: str, unit: str = ""):
        self.name = name
        self.unit = unit
        self._data: deque = deque(maxlen=_MAX_POINTS)
        self._lock = threading.Lock()

    def record(self, value: float, ts: Optional[float] = None) -> None:
        with self._lock:
            self._data.append((ts or time.time(), value))

    def snapshot(self, since: float = 0) -> List[List[float]]:
        """Return [[ts, value], ...] for points with ts >= *since*."""
        with self._lock:
            if since:
                return [[t, v] for t, v in self._data if t >= since]
            return [list(p) for p in self._data]


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------

class _Counter:
    """Monotonically increasing counter, thread-safe."""

    __slots__ = ("name", "_value", "_lock")

    def __init__(self, name: str):
        self.name = name
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, n: int = 1) -> None:
        with self._lock:
            self._value += n

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


# ---------------------------------------------------------------------------
# Gauges
# ---------------------------------------------------------------------------

class _Gauge:
    """Point-in-time value, thread-safe."""

    __slots__ = ("name", "unit", "_value", "_lock")

    def __init__(self, name: str, unit: str = ""):
        self.name = name
        self.unit = unit
        self._value: float = 0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    @property
    def value(self) -> float:
        with self._lock:
            return self._value


# ---------------------------------------------------------------------------
# Recent log entries ring buffer
# ---------------------------------------------------------------------------

_MAX_LOG_ENTRIES = 500


class _LogRing:
    """Ring buffer for recent structured log entries shown in the dashboard."""

    def __init__(self, maxlen: int = _MAX_LOG_ENTRIES):
        self._data: deque = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def append(self, entry: Dict[str, Any]) -> None:
        with self._lock:
            self._data.append(entry)

    def snapshot(self, since: float = 0, limit: int = 200) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._data)
        if since:
            items = [e for e in items if e.get("ts", 0) >= since]
        return items[-limit:]


# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------

class MetricsRegistry:
    """Singleton-ish registry of all metrics.  Importable as ``metrics``."""

    def __init__(self):
        # Counters
        self.api_calls = _Counter("api_calls_total")
        self.api_errors = _Counter("api_errors_total")
        self.throttle_events = _Counter("throttle_events_total")
        self.messages_sent = _Counter("messages_sent_total")
        self.messages_received = _Counter("messages_received_total")
        self.ws_connections = _Counter("ws_connections_total")
        self.ws_disconnections = _Counter("ws_disconnections_total")

        # Gauges
        self.active_clients = _Gauge("active_clients", "connections")
        self.refresh_interval = _Gauge("refresh_interval", "seconds")
        self.cache_size = _Gauge("cache_size", "stops")

        # Time series
        self.api_latency = _TimeSeries("api_latency_ms", "ms")
        self.refresh_interval_ts = _TimeSeries("refresh_interval_s", "s")
        self.active_clients_ts = _TimeSeries("active_clients", "")
        self.messages_rate_ts = _TimeSeries("messages_per_interval", "msg")
        self.throttle_rate_ts = _TimeSeries("throttle_rate", "%")
        self.api_calls_ts = _TimeSeries("api_calls_per_interval", "calls")

        # Log ring
        self.logs = _LogRing()

        # Start time
        self.start_time = time.time()

    def snapshot(self, series_since: float = 0) -> Dict[str, Any]:
        """Full metrics snapshot suitable for JSON serialization."""
        now = time.time()
        return {
            "ts": now,
            "uptime_s": now - self.start_time,
            "counters": {
                "api_calls": self.api_calls.value,
                "api_errors": self.api_errors.value,
                "throttle_events": self.throttle_events.value,
                "messages_sent": self.messages_sent.value,
                "messages_received": self.messages_received.value,
                "ws_connections": self.ws_connections.value,
                "ws_disconnections": self.ws_disconnections.value,
            },
            "gauges": {
                "active_clients": self.active_clients.value,
                "refresh_interval_s": self.refresh_interval.value,
                "cache_size": self.cache_size.value,
            },
            "series": {
                "api_latency_ms": self.api_latency.snapshot(series_since),
                "refresh_interval_s": self.refresh_interval_ts.snapshot(series_since),
                "active_clients": self.active_clients_ts.snapshot(series_since),
                "messages_per_interval": self.messages_rate_ts.snapshot(series_since),
                "throttle_rate": self.throttle_rate_ts.snapshot(series_since),
                "api_calls_per_interval": self.api_calls_ts.snapshot(series_since),
            },
        }


# Module-level singleton
metrics = MetricsRegistry()
