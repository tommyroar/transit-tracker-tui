"""Unit tests for the InfluxDB writer.

`monkeypatch` swaps `urllib.request.urlopen` so we can capture POST bodies
without a real InfluxDB. Mirrors the test style of `test_metrics.py`
(pytestmark = unit) and the tempest-bridge writer in home-weather-hub.
"""

from __future__ import annotations

import io
import logging
import time
import urllib.error
import urllib.request
from typing import List, Tuple

import pytest

from transit_tracker.observability import influxdb_writer
from transit_tracker.observability.influxdb_writer import (
    InfluxDBWriter,
    build_line,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _CapturingTransport:
    """Records every urlopen call; returns a 204 response by default."""

    def __init__(self, status: int = 204):
        self.status = status
        self.calls: List[Tuple[str, bytes, dict]] = []

    def __call__(self, req: urllib.request.Request, timeout: float = 5):
        body = req.data if isinstance(req.data, (bytes, bytearray)) else b""
        self.calls.append((req.full_url, bytes(body), dict(req.headers)))
        return _FakeResponse(self.status)


class _FakeResponse:
    def __init__(self, status: int):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b""


def _flush(writer: InfluxDBWriter, timeout: float = 2.0) -> None:
    """Block until the writer's queue drains or *timeout* elapses."""
    deadline = time.monotonic() + timeout
    while writer.qsize() > 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    # one extra tick to ensure the worker has flushed
    time.sleep(writer.flush_interval_s + 0.2)


def make_sample_trip() -> dict:
    return {
        "tripId": "st:t1",
        "routeId": "st:40_100240",
        "routeName": "554",
        "routeColor": "2B376E",
        "stopId": "st:1_8494",
        "headsign": "Downtown Seattle",
        "arrivalTime": 1779_000_000,
        "departureTime": 1779_000_030,
        "isRealtime": True,
        "scheduledArrivalTime": 1778_999_940,
    }


# ---------------------------------------------------------------------------
# Line-protocol builder
# ---------------------------------------------------------------------------


class TestBuildLine:
    def test_renders_tags_and_typed_fields(self):
        line = build_line(
            "trip_prediction",
            {"route_id": "st:40", "stop_id": "1_8494", "is_realtime": "true"},
            {"arrival_time_s": 1779000000, "predicted_offset_s": 12.5, "trip_id": "st:t1"},
            ts_seconds=1779000123,
        )
        # measurement + sorted tags (insertion order preserved)
        assert line.startswith(
            "trip_prediction,route_id=st:40,stop_id=1_8494,is_realtime=true "
        )
        # int field has the 'i' suffix
        assert "arrival_time_s=1779000000i" in line
        # float field bare
        assert "predicted_offset_s=12.5" in line
        # string field quoted
        assert 'trip_id="st:t1"' in line
        # timestamp at the end, integer seconds
        assert line.endswith(" 1779000123")

    def test_escapes_tag_specials(self):
        line = build_line(
            "service_gauge",
            {"name": "rate, per minute", "unit": "msg=fmt"},
            {"value": 1.0},
            ts_seconds=1,
        )
        assert "name=rate\\,\\ per\\ minute" in line
        assert "unit=msg\\=fmt" in line

    def test_returns_none_when_no_fields(self):
        assert build_line("m", {}, {}, ts_seconds=1) is None
        # All-None field values also drop the point
        assert build_line("m", {}, {"x": None}, ts_seconds=1) is None


# ---------------------------------------------------------------------------
# Writer behavior
# ---------------------------------------------------------------------------


class TestWriterEnabled:
    """Writer with a token — should serialize and POST line protocol."""

    @pytest.fixture
    def transport(self, monkeypatch):
        t = _CapturingTransport()
        monkeypatch.setattr(urllib.request, "urlopen", t)
        return t

    @pytest.fixture
    def writer(self, transport):
        w = InfluxDBWriter(
            url="http://influxdb:8086",
            token="testtoken",
            org="home",
            bucket="transit_tracker_test",
            flush_interval_s=0.1,
        )
        yield w
        w.shutdown(timeout=2)

    def test_enqueue_trip_writes_trip_prediction(self, writer, transport):
        writer.enqueue_trip(make_sample_trip(), ts_seconds=1779000123)
        _flush(writer)
        assert len(transport.calls) == 1
        url, body, headers = transport.calls[0]
        assert "bucket=transit_tracker_test" in url
        assert headers.get("Authorization") == "Token testtoken"
        decoded = body.decode("utf-8")
        assert decoded.startswith("trip_prediction,")
        assert "route_id=st:40_100240" in decoded
        assert "stop_id=st:1_8494" in decoded
        assert "is_realtime=true" in decoded
        # predicted_offset = arrivalTime - scheduledArrivalTime = 60s
        assert "predicted_offset_s=60.0" in decoded
        assert decoded.endswith(" 1779000123")

    def test_enqueue_counter_writes_service_counter(self, writer, transport):
        writer.enqueue_counter("api_calls_total", 42, ts_seconds=1779000100)
        _flush(writer)
        body = transport.calls[0][1].decode("utf-8")
        assert body.startswith("service_counter,name=api_calls_total ")
        assert "value=42i" in body

    def test_enqueue_gauge_writes_service_gauge(self, writer, transport):
        writer.enqueue_gauge("active_clients", 3.0, "connections", ts_seconds=1779000100)
        _flush(writer)
        body = transport.calls[0][1].decode("utf-8")
        assert body.startswith("service_gauge,")
        assert "name=active_clients" in body
        assert "unit=connections" in body
        assert "value=3.0" in body

    def test_batches_multiple_points_into_single_request(self, writer, transport):
        for i in range(5):
            writer.enqueue_counter("c", i, ts_seconds=1779000000 + i)
        _flush(writer)
        # One POST containing 5 lines
        assert len(transport.calls) == 1
        body = transport.calls[0][1].decode("utf-8")
        assert body.count("\n") == 4

    def test_http_error_increments_influx_errors(self, monkeypatch):
        from transit_tracker.metrics import metrics

        before = metrics.influx_errors.value

        def _raise(req, timeout=5):
            raise urllib.error.HTTPError(
                url=req.full_url,
                code=500,
                msg="boom",
                hdrs=None,
                fp=io.BytesIO(b"server error"),
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        w = InfluxDBWriter(
            url="http://influxdb:8086",
            token="t",
            org="home",
            bucket="b",
            flush_interval_s=0.1,
        )
        try:
            w.enqueue_counter("c", 1)
            _flush(w)
        finally:
            w.shutdown(timeout=2)
        assert metrics.influx_errors.value > before


class TestFailureLogRateLimiting:
    """A multi-hour outage must not flood the logs (see _FAIL_LOG_INTERVAL_S).

    Uses a token-less (thread-less) writer so the rate-limit helpers can be
    driven directly against a fake monotonic clock. The module logger has
    propagate=False, so we attach our own recording handler rather than caplog.
    """

    @pytest.fixture
    def records(self):
        captured: List[logging.LogRecord] = []

        class _Recorder(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _Recorder()
        logger = influxdb_writer.log
        prev_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            yield captured
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev_level)

    def _writer(self):
        # token="" => no background thread; failure-state attrs still init.
        return InfluxDBWriter(url="http://influxdb:8086", token="", org="home", bucket="b")

    def _at(self, monkeypatch, t):
        clock = {"t": t}
        monkeypatch.setattr(influxdb_writer.time, "monotonic", lambda: clock["t"])
        return clock

    def test_first_failure_logs_then_repeats_are_suppressed(self, monkeypatch, records):
        clock = self._at(monkeypatch, 1000.0)
        w = self._writer()

        w._note_failure("influx write failed: boom")  # logs immediately
        for _ in range(20):  # all within the interval -> suppressed
            clock["t"] += 2.0
            w._note_failure("influx write failed: boom")

        warnings = [r for r in records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert w._fail_streak == 21
        assert w._fail_suppressed == 20

    def test_summary_emitted_once_per_interval(self, monkeypatch, records):
        clock = self._at(monkeypatch, 1000.0)
        w = self._writer()

        w._note_failure("boom")            # 1st -> logs
        clock["t"] += 30.0
        w._note_failure("boom")            # within 60s -> suppressed
        clock["t"] += 31.0                 # now > 60s since last log
        w._note_failure("boom")            # -> summary line

        warnings = [r for r in records if r.levelno == logging.WARNING]
        assert len(warnings) == 2
        assert "suppressed" in warnings[1].getMessage()

    def test_success_logs_recovery_and_resets(self, monkeypatch, records):
        clock = self._at(monkeypatch, 1000.0)
        w = self._writer()

        w._note_failure("boom")
        clock["t"] += 2.0
        w._note_failure("boom")
        w._note_success()

        recovery = [r for r in records if "recovered" in r.getMessage()]
        assert len(recovery) == 1
        assert "2 consecutive failures" in recovery[0].getMessage()
        assert w._fail_streak == 0 and w._fail_suppressed == 0

    def test_success_without_prior_failure_is_silent(self, monkeypatch, records):
        self._at(monkeypatch, 1000.0)
        w = self._writer()
        w._note_success()
        assert [r for r in records if "recovered" in r.getMessage()] == []


class TestWriterDisabled:
    """No token -> no-op; nothing should hit urlopen."""

    def test_enqueue_returns_cleanly_when_token_unset(self, monkeypatch):
        t = _CapturingTransport()
        monkeypatch.setattr(urllib.request, "urlopen", t)
        w = InfluxDBWriter(url="http://influxdb:8086", token="", org="home", bucket="b")
        w.enqueue_trip(make_sample_trip())
        w.enqueue_counter("c", 1)
        w.enqueue_gauge("g", 2.0)
        # Give any rogue thread a moment to act (it shouldn't exist).
        time.sleep(0.1)
        assert t.calls == []
        assert w.enabled is False


class TestIdleDoesNotBusySpin:
    """An idle writer (empty queue past its flush deadline) must block on
    `get()`, not busy-spin. Regression test for the 100% CPU bug where the
    flush deadline was only reset alongside a non-empty batch, pinning the
    `get()` timeout to 0 and looping the worker thread tightly forever.
    """

    def test_empty_queue_blocks_instead_of_spinning(self, monkeypatch):
        t = _CapturingTransport()
        monkeypatch.setattr(urllib.request, "urlopen", t)

        # Count how many times the worker calls queue.get while idle.
        w = InfluxDBWriter(
            url="http://influxdb:8086",
            token="t",
            org="home",
            bucket="b",
            flush_interval_s=0.1,
        )
        try:
            calls = {"n": 0}
            real_get = w._queue.get

            def _counting_get(*args, **kwargs):
                calls["n"] += 1
                return real_get(*args, **kwargs)

            monkeypatch.setattr(w._queue, "get", _counting_get)

            # Sit idle for several flush intervals with nothing enqueued.
            time.sleep(0.6)

            # With the fix, get() is called ~once per flush_interval (~6 over
            # 0.6s). The busy-spin bug called it many thousands of times.
            assert calls["n"] < 50, (
                f"writer busy-spun: {calls['n']} get() calls while idle"
            )
        finally:
            w.shutdown(timeout=2)


class TestBackpressure:
    """Bounded queue drops on full and bumps influx_drops."""

    def test_full_queue_drops_and_bumps_counter(self, monkeypatch):
        from transit_tracker.metrics import metrics

        # Hold the worker thread by stalling urlopen. With urlopen blocked,
        # enqueued items pile up until the queue is full.
        gate = __import__("threading").Event()

        def _blocking(req, timeout=5):
            gate.wait(timeout=2)
            return _FakeResponse(204)

        monkeypatch.setattr(urllib.request, "urlopen", _blocking)
        w = InfluxDBWriter(
            url="http://influxdb:8086",
            token="t",
            org="home",
            bucket="b",
            maxsize=4,
            batch_size=1,
            flush_interval_s=0.05,
        )
        try:
            before = metrics.influx_drops.value
            for i in range(50):
                w.enqueue_counter("c", i)
            assert metrics.influx_drops.value > before
        finally:
            gate.set()
            w.shutdown(timeout=2)
