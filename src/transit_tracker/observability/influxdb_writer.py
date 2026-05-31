"""Background InfluxDB writer for transit_tracker.

Mirrors every counter, gauge, time-series sample, and broadcast trip
into InfluxDB line protocol. Batched, non-blocking, drops on backpressure
rather than blocking the main loop. Disabled-by-default: if INFLUXDB_TOKEN
is unset the writer is a no-op, the rest of the service is unaffected.

Stdlib only (urllib + threading + queue) so no new pyproject deps.
Pattern mirrors home-weather-hub's tempest_to_influx.py.
"""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional

log = logging.getLogger("transit_tracker.influxdb")

# ---------------------------------------------------------------------------
# Line-protocol escaping
# ---------------------------------------------------------------------------

_TAG_SPECIALS = str.maketrans(
    {",": r"\,", " ": r"\ ", "=": r"\="}
)

_FIELD_STR_SPECIALS = str.maketrans({'"': r"\"", "\\": r"\\"})


def _esc_tag(s: Any) -> str:
    """Escape commas/spaces/equals in tag keys and values."""
    return str(s).translate(_TAG_SPECIALS)


def _esc_field_str(s: Any) -> str:
    """Escape quotes/backslashes in string-typed field values."""
    return str(s).translate(_FIELD_STR_SPECIALS)


def _kv_tags(tags: Mapping[str, Any]) -> str:
    """Render `,k=v,k=v` (with leading comma) for non-empty tags."""
    parts = []
    for k, v in tags.items():
        if v is None or v == "":
            continue
        parts.append(f"{_esc_tag(k)}={_esc_tag(v)}")
    return ("," + ",".join(parts)) if parts else ""


def _kv_fields(fields: Mapping[str, Any]) -> str:
    """Render `k=v,k=v` with type suffixes appropriate for the value's Python type."""
    parts = []
    for k, v in fields.items():
        if v is None:
            continue
        if isinstance(v, bool):
            parts.append(f"{_esc_tag(k)}={'true' if v else 'false'}")
        elif isinstance(v, int):
            parts.append(f"{_esc_tag(k)}={v}i")
        elif isinstance(v, float):
            parts.append(f"{_esc_tag(k)}={v}")
        else:
            parts.append(f'{_esc_tag(k)}="{_esc_field_str(v)}"')
    return ",".join(parts)


def build_line(
    measurement: str,
    tags: Mapping[str, Any],
    fields: Mapping[str, Any],
    ts_seconds: float,
) -> Optional[str]:
    """Construct one InfluxDB line-protocol point. Returns None if no fields render."""
    field_kv = _kv_fields(fields)
    if not field_kv:
        return None
    return f"{_esc_tag(measurement)}{_kv_tags(tags)} {field_kv} {int(ts_seconds)}"


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

_DEFAULT_MAXSIZE = 5000
_DEFAULT_BATCH = 500
_DEFAULT_FLUSH_INTERVAL = 2.0


class InfluxDBWriter:
    """Background writer that POSTs line-protocol batches to InfluxDB.

    `enqueue_*` methods are non-blocking; the work happens on a daemon
    thread. When the bounded queue is full, points are dropped and the
    `metrics.influx_drops` counter is incremented (lazy import to avoid
    a circular dependency at module load).
    """

    def __init__(
        self,
        url: str,
        token: str,
        org: str,
        bucket: str,
        *,
        maxsize: int = _DEFAULT_MAXSIZE,
        batch_size: int = _DEFAULT_BATCH,
        flush_interval_s: float = _DEFAULT_FLUSH_INTERVAL,
    ):
        self.url = url.rstrip("/")
        self.token = token
        self.org = org
        self.bucket = bucket
        self.batch_size = batch_size
        self.flush_interval_s = flush_interval_s
        self.enabled = bool(token)
        self._queue: queue.Queue[str] = queue.Queue(maxsize=maxsize)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        if self.enabled:
            self._thread = threading.Thread(
                target=self._run, name="influxdb-writer", daemon=True
            )
            self._thread.start()
            log.info(
                "InfluxDB writer started: url=%s org=%s bucket=%s",
                self.url, self.org, self.bucket,
            )
        else:
            log.info("InfluxDB writer disabled (INFLUXDB_TOKEN unset)")

    # ----- Public API: non-blocking enqueue helpers --------------------------

    def enqueue_trip(self, trip: Mapping[str, Any], ts_seconds: Optional[float] = None) -> None:
        """Mirror one broadcast trip dict into a `trip_prediction` point."""
        if not self.enabled:
            return
        ts = ts_seconds if ts_seconds is not None else time.time()
        tags = {
            "route_id": trip.get("routeId"),
            "stop_id": trip.get("stopId"),
            "route_name": trip.get("routeName"),
            "headsign": trip.get("headsign"),
            "is_realtime": "true" if trip.get("isRealtime") else "false",
        }
        fields: dict[str, Any] = {}
        arr = trip.get("arrivalTime")
        dep = trip.get("departureTime")
        sched_arr = trip.get("scheduledArrivalTime")
        if isinstance(arr, (int, float)):
            fields["arrival_time_s"] = int(arr)
        if isinstance(dep, (int, float)):
            fields["departure_time_s"] = int(dep)
        if isinstance(arr, (int, float)) and isinstance(sched_arr, (int, float)):
            fields["predicted_offset_s"] = float(arr) - float(sched_arr)
        trip_id = trip.get("tripId")
        if trip_id:
            fields["trip_id"] = str(trip_id)
        line = build_line("trip_prediction", tags, fields, ts)
        self._submit(line)

    def enqueue_counter(self, name: str, value: int, ts_seconds: Optional[float] = None) -> None:
        if not self.enabled:
            return
        ts = ts_seconds if ts_seconds is not None else time.time()
        line = build_line("service_counter", {"name": name}, {"value": int(value)}, ts)
        self._submit(line)

    def enqueue_gauge(
        self,
        name: str,
        value: float,
        unit: str = "",
        ts_seconds: Optional[float] = None,
    ) -> None:
        if not self.enabled:
            return
        ts = ts_seconds if ts_seconds is not None else time.time()
        line = build_line(
            "service_gauge",
            {"name": name, "unit": unit},
            {"value": float(value)},
            ts,
        )
        self._submit(line)

    def shutdown(self, timeout: float = 5.0) -> None:
        """Signal the worker thread and wait for it (used in tests)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def qsize(self) -> int:
        return self._queue.qsize()

    # ----- Internals --------------------------------------------------------

    def _submit(self, line: Optional[str]) -> None:
        if line is None:
            return
        try:
            self._queue.put_nowait(line)
        except queue.Full:
            _bump("influx_drops")

    def _run(self) -> None:
        """Drain the queue into batches and POST them."""
        deadline = time.monotonic() + self.flush_interval_s
        batch: list[str] = []
        while not self._stop.is_set():
            timeout = max(0.0, deadline - time.monotonic())
            try:
                line = self._queue.get(timeout=timeout)
                batch.append(line)
            except queue.Empty:
                pass

            now = time.monotonic()
            if len(batch) >= self.batch_size or now >= deadline:
                # Always reset the deadline when it expires, even with an empty
                # batch — otherwise `timeout` pins to 0 and `get()` busy-spins
                # the thread at 100% CPU whenever no data is enqueued.
                if batch:
                    self._flush(batch)
                    batch = []
                deadline = now + self.flush_interval_s
        # Drain whatever remains after stop is signalled.
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._flush(batch)

    def _flush(self, batch: list[str]) -> None:
        body = "\n".join(batch).encode("utf-8")
        url = (
            f"{self.url}/api/v2/write?org={self.org}"
            f"&bucket={self.bucket}&precision=s"
        )
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Token {self.token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if 200 <= resp.status < 300:
                    _bump("influx_writes", n=len(batch))
                else:
                    _bump("influx_errors")
                    log.warning("influx write returned HTTP %s", resp.status)
        except urllib.error.HTTPError as e:
            _bump("influx_errors")
            log.warning(
                "influx HTTP %s: %s",
                e.code,
                e.read().decode("utf-8", "replace")[:300],
            )
        except OSError as e:
            _bump("influx_errors")
            log.warning("influx write failed: %s", e)


# ---------------------------------------------------------------------------
# Self-instrumentation helper (lazy import to dodge circularity)
# ---------------------------------------------------------------------------

def _bump(counter_name: str, n: int = 1) -> None:
    """Increment a counter in transit_tracker.metrics if available."""
    try:
        from transit_tracker import metrics as _m  # local to dodge circular import

        counter = getattr(_m.metrics, counter_name, None)
        if counter is not None:
            counter.inc(n)
    except Exception:
        # Never let observability accounting kill the writer thread.
        pass


# ---------------------------------------------------------------------------
# Module-level singleton built from env vars
# ---------------------------------------------------------------------------

def _make_default_writer() -> InfluxDBWriter:
    return InfluxDBWriter(
        url=os.environ.get("INFLUXDB_URL", "http://influxdb:8086"),
        token=os.environ.get("INFLUXDB_TOKEN", ""),
        org=os.environ.get("INFLUXDB_ORG", "home"),
        bucket=os.environ.get("INFLUXDB_BUCKET", "transit_tracker"),
        batch_size=int(os.environ.get("INFLUXDB_BATCH_SIZE", _DEFAULT_BATCH)),
        flush_interval_s=float(
            os.environ.get("INFLUXDB_FLUSH_INTERVAL_S", _DEFAULT_FLUSH_INTERVAL)
        ),
    )


influx: InfluxDBWriter = _make_default_writer()
