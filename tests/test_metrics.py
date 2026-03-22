"""Unit tests for the metrics module.


Tests the thread-safe counters, gauges, time-series ring buffers,
log ring buffer, and the MetricsRegistry snapshot.
"""

import pytest

from transit_tracker.metrics import (
    _MAX_POINTS,
    MetricsRegistry,
    _Counter,
    _Gauge,
    _LogRing,
    _TimeSeries,
)

pytestmark = pytest.mark.unit

# -- Counter -----------------------------------------------------------------


class TestCounter:
    def test_initial_value(self):
        c = _Counter("test")
        assert c.value == 0

    def test_increment(self):
        c = _Counter("test")
        c.inc()
        assert c.value == 1

    def test_increment_by_n(self):
        c = _Counter("test")
        c.inc(5)
        assert c.value == 5

    def test_multiple_increments(self):
        c = _Counter("test")
        c.inc(3)
        c.inc(2)
        assert c.value == 5


# -- Gauge -------------------------------------------------------------------


class TestGauge:
    def test_initial_value(self):
        g = _Gauge("test")
        assert g.value == 0

    def test_set(self):
        g = _Gauge("test")
        g.set(42.5)
        assert g.value == 42.5

    def test_overwrite(self):
        g = _Gauge("test")
        g.set(10)
        g.set(20)
        assert g.value == 20


# -- TimeSeries --------------------------------------------------------------


class TestTimeSeries:
    def test_record_and_snapshot(self):
        ts = _TimeSeries("test")
        ts.record(1.0, ts=100.0)
        ts.record(2.0, ts=200.0)
        snap = ts.snapshot()
        assert len(snap) == 2
        assert snap[0] == [100.0, 1.0]
        assert snap[1] == [200.0, 2.0]

    def test_snapshot_since(self):
        ts = _TimeSeries("test")
        ts.record(1.0, ts=100.0)
        ts.record(2.0, ts=200.0)
        ts.record(3.0, ts=300.0)
        snap = ts.snapshot(since=200.0)
        assert len(snap) == 2
        assert snap[0] == [200.0, 2.0]

    def test_ring_overflow(self):
        ts = _TimeSeries("test")
        for i in range(_MAX_POINTS + 10):
            ts.record(float(i), ts=float(i))
        snap = ts.snapshot()
        assert len(snap) == _MAX_POINTS
        # Oldest points should be dropped
        assert snap[0][0] == 10.0

    def test_snapshot_returns_copies(self):
        ts = _TimeSeries("test")
        ts.record(1.0, ts=100.0)
        snap = ts.snapshot()
        snap[0][1] = 999
        assert ts.snapshot()[0][1] == 1.0


# -- LogRing -----------------------------------------------------------------


class TestLogRing:
    def test_append_and_snapshot(self):
        lr = _LogRing()
        lr.append({"ts": 1.0, "msg": "hello"})
        snap = lr.snapshot()
        assert len(snap) == 1
        assert snap[0]["msg"] == "hello"

    def test_snapshot_since(self):
        lr = _LogRing()
        lr.append({"ts": 1.0, "msg": "old"})
        lr.append({"ts": 2.0, "msg": "new"})
        snap = lr.snapshot(since=1.5)
        assert len(snap) == 1
        assert snap[0]["msg"] == "new"

    def test_snapshot_limit(self):
        lr = _LogRing()
        for i in range(10):
            lr.append({"ts": float(i), "msg": f"msg{i}"})
        snap = lr.snapshot(limit=3)
        assert len(snap) == 3
        # Should be the last 3 entries
        assert snap[0]["msg"] == "msg7"

    def test_overflow(self):
        lr = _LogRing(maxlen=5)
        for i in range(10):
            lr.append({"ts": float(i), "msg": f"msg{i}"})
        snap = lr.snapshot()
        assert len(snap) == 5
        assert snap[0]["msg"] == "msg5"


# -- MetricsRegistry ---------------------------------------------------------


class TestMetricsRegistry:
    def test_snapshot_structure(self):
        reg = MetricsRegistry()
        snap = reg.snapshot()
        assert "ts" in snap
        assert "uptime_s" in snap
        assert "counters" in snap
        assert "gauges" in snap
        assert "series" in snap

    def test_snapshot_counters(self):
        reg = MetricsRegistry()
        reg.api_calls.inc(3)
        snap = reg.snapshot()
        assert snap["counters"]["api_calls"] == 3

    def test_snapshot_gauges(self):
        reg = MetricsRegistry()
        reg.active_clients.set(5)
        snap = reg.snapshot()
        assert snap["gauges"]["active_clients"] == 5

    def test_snapshot_series_keys(self):
        reg = MetricsRegistry()
        snap = reg.snapshot()
        assert "api_latency_ms" in snap["series"]
        assert "refresh_interval_s" in snap["series"]
        assert "active_clients" in snap["series"]
