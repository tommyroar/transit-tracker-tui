"""Integration test against the real local InfluxDB.

Skips unless `INFLUX_ADMIN_TOKEN` is exported (and the local InfluxDB at
`INFLUX_URL` — default http://localhost:8086 — is reachable). Creates a
throwaway `transit_tracker_test` bucket, writes a handful of points via
`InfluxDBWriter`, flushes, queries them back, and tears the bucket down.

Run only this file: `INFLUX_ADMIN_TOKEN=... uv run pytest tests/test_influxdb_integration.py -m integration -v`.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Tuple

import pytest

from transit_tracker.observability.influxdb_writer import InfluxDBWriter

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Skip logic + connection helpers
# ---------------------------------------------------------------------------


_ADMIN_TOKEN = os.environ.get("INFLUX_ADMIN_TOKEN", "").strip()
_INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8086").rstrip("/")
_INFLUX_ORG = os.environ.get("INFLUX_ORG", "home")
_TEST_BUCKET = "transit_tracker_test"


def _influx_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{_INFLUX_URL}/health", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


skip_reason: Optional[str] = None
if not _ADMIN_TOKEN:
    skip_reason = "INFLUX_ADMIN_TOKEN not set"
elif not _influx_reachable():
    skip_reason = f"InfluxDB not reachable at {_INFLUX_URL}"

if skip_reason:
    pytest.skip(skip_reason, allow_module_level=True)


def _api(method: str, path: str, *, body: Optional[dict] = None, accept: str = "application/json") -> Tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{_INFLUX_URL}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Token {_ADMIN_TOKEN}",
            "Content-Type": "application/json",
            "Accept": accept,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _org_id() -> str:
    code, payload = _api("GET", f"/api/v2/orgs?org={urllib.parse.quote(_INFLUX_ORG)}")
    assert code == 200, payload
    orgs = json.loads(payload).get("orgs", [])
    assert orgs, f"org '{_INFLUX_ORG}' not found"
    return orgs[0]["id"]


def _find_bucket(name: str) -> Optional[dict]:
    code, payload = _api("GET", f"/api/v2/buckets?name={urllib.parse.quote(name)}")
    assert code == 200, payload
    for b in json.loads(payload).get("buckets", []):
        if b["name"] == name:
            return b
    return None


# ---------------------------------------------------------------------------
# Fixture: ephemeral test bucket
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def test_bucket():
    org_id = _org_id()
    existing = _find_bucket(_TEST_BUCKET)
    if existing:
        _api("DELETE", f"/api/v2/buckets/{existing['id']}")
    code, payload = _api(
        "POST",
        "/api/v2/buckets",
        body={
            "orgID": org_id,
            "name": _TEST_BUCKET,
            "retentionRules": [],
        },
    )
    assert code in (201, 200), payload
    bucket = json.loads(payload)
    yield bucket
    _api("DELETE", f"/api/v2/buckets/{bucket['id']}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _flush(writer: InfluxDBWriter, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while writer.qsize() > 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(writer.flush_interval_s + 0.3)


def _query_count(measurement: str, since: str = "-2m") -> int:
    flux = (
        f'from(bucket:"{_TEST_BUCKET}") '
        f"|> range(start: {since}) "
        f'|> filter(fn:(r) => r._measurement == "{measurement}") '
        f"|> count()"
    )
    req = urllib.request.Request(
        f"{_INFLUX_URL}/api/v2/query?org={urllib.parse.quote(_INFLUX_ORG)}",
        data=flux.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Token {_ADMIN_TOKEN}",
            "Content-Type": "application/vnd.flux",
            "Accept": "application/csv",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        csv_body = resp.read().decode("utf-8")
    # Annotated-CSV from Flux: comment rows start with '#', the header row
    # starts with ',result,table,...', and `_value` may be at any column.
    # Sum the _value cells across every data row (count() emits one row per
    # output series, with _value = the count for that series).
    total = 0
    value_idx: Optional[int] = None
    for line in csv_body.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if value_idx is None:
            # Header row: first cell is empty, second is 'result'.
            if len(parts) >= 2 and parts[1] == "result":
                try:
                    value_idx = parts.index("_value")
                except ValueError:
                    return 0
            continue
        if value_idx >= len(parts):
            continue
        try:
            total += int(parts[value_idx])
        except (ValueError, IndexError):
            continue
    return total


def test_trip_and_metrics_round_trip(test_bucket):
    """End-to-end: enqueue trip + counter + gauge, then query them back."""
    writer = InfluxDBWriter(
        url=_INFLUX_URL,
        token=_ADMIN_TOKEN,
        org=_INFLUX_ORG,
        bucket=_TEST_BUCKET,
        flush_interval_s=0.2,
        batch_size=10,
    )
    try:
        now = int(time.time())
        for i in range(3):
            writer.enqueue_trip(
                {
                    "tripId": f"st:t{i}",
                    "routeId": "st:40_test",
                    "routeName": "TEST",
                    "stopId": "st:test_stop",
                    "headsign": "Pytest Land",
                    "arrivalTime": now + i * 60,
                    "departureTime": now + i * 60 + 30,
                    "isRealtime": True,
                    "scheduledArrivalTime": now + i * 60 - 15,
                },
                ts_seconds=now + i,
            )
        writer.enqueue_counter("api_calls_total", 7, ts_seconds=now)
        writer.enqueue_gauge("active_clients", 2.0, "connections", ts_seconds=now)
        _flush(writer)
    finally:
        writer.shutdown(timeout=5)

    assert _query_count("trip_prediction") >= 3, "expected at least 3 trip_prediction points"
    assert _query_count("service_counter") >= 1, "expected at least 1 service_counter point"
    assert _query_count("service_gauge") >= 1, "expected at least 1 service_gauge point"
