"""Tests for the 'served from GTFS schedule only' warning in TransitServer.

When a subscribed stop has no OBA realtime (e.g. the Judkins Park / 40_E01
upstream feed gap), the sign falls back to GTFS schedule and shows no live
marker. `_note_stop_data_source` surfaces that — edge-triggered with hysteresis
so a normal gap between trains doesn't trip it. Driven here against a fake
monotonic clock. The server logger has propagate=False, so we attach our own
recording handler rather than caplog.
"""

import logging
from unittest.mock import MagicMock

import pytest

from transit_tracker.config import TransitSubscription
from transit_tracker.network import websocket_server
from transit_tracker.network.websocket_server import (
    _GTFS_ONLY_WARN_AFTER_S,
    _GTFS_ONLY_WARN_INTERVAL_S,
    TransitServer,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def server():
    config = MagicMock()
    config.subscriptions = [
        TransitSubscription(
            feed="st", route="st:40_2LINE", stop="st:40_E01-T1", label="JP"
        )
    ]
    config.service = MagicMock()
    config.service.oba_api_key = None
    config.service.check_interval_seconds = 30
    config.service.display_brightness = 128
    config.service.use_local_api = True
    config.transit_tracker = MagicMock()
    config.transit_tracker.time_display = "arrival"
    config.transit_tracker.abbreviations = []
    return TransitServer(config)


@pytest.fixture
def records():
    captured = []

    class _Recorder(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = _Recorder()
    logger = websocket_server.log
    prev = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield captured
    finally:
        logger.removeHandler(handler)
        logger.setLevel(prev)


def _clock(monkeypatch, t):
    box = {"t": t}
    monkeypatch.setattr(websocket_server.time, "monotonic", lambda: box["t"])
    return box


def _warnings(records):
    return [r for r in records if r.levelno == logging.WARNING]


SID = "st:40_E01-T1"


def test_no_warning_within_grace_period(server, records, monkeypatch):
    clock = _clock(monkeypatch, 1000.0)
    # GTFS-only from cold start, but still inside the grace window.
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)
    clock["t"] += _GTFS_ONLY_WARN_AFTER_S - 1
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)
    assert _warnings(records) == []


def test_warns_after_grace_period(server, records, monkeypatch):
    clock = _clock(monkeypatch, 1000.0)
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)  # starts clock
    clock["t"] += _GTFS_ONLY_WARN_AFTER_S + 1
    server._note_stop_data_source(
        SID, oba_realtime_count=0, produced=2
    )  # crosses grace
    w = _warnings(records)
    assert len(w) == 1
    assert "GTFS schedule only" in w[0].getMessage()


def test_no_warning_when_no_trips_produced(server, records, monkeypatch):
    clock = _clock(monkeypatch, 1000.0)
    for _ in range(5):
        clock["t"] += _GTFS_ONLY_WARN_AFTER_S
        server._note_stop_data_source(SID, oba_realtime_count=0, produced=0)
    assert _warnings(records) == []


def test_realtime_seen_resets_the_clock(server, records, monkeypatch):
    clock = _clock(monkeypatch, 1000.0)
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)
    clock["t"] += _GTFS_ONLY_WARN_AFTER_S - 5
    server._note_stop_data_source(
        SID, oba_realtime_count=3, produced=3
    )  # realtime → reset
    clock["t"] += _GTFS_ONLY_WARN_AFTER_S - 5  # < grace since reset
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)
    assert _warnings(records) == []


def test_recovery_logged_and_state_cleared(server, records, monkeypatch):
    clock = _clock(monkeypatch, 1000.0)
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)
    clock["t"] += _GTFS_ONLY_WARN_AFTER_S + 1
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)  # warns
    server._note_stop_data_source(SID, oba_realtime_count=1, produced=2)  # recovery
    assert len(_warnings(records)) == 1
    resumed = [r for r in records if "resumed" in r.getMessage()]
    assert len(resumed) == 1
    assert SID not in server._gtfs_only_warned


def test_rewarns_only_after_interval(server, records, monkeypatch):
    clock = _clock(monkeypatch, 1000.0)
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)
    clock["t"] += _GTFS_ONLY_WARN_AFTER_S + 1
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)  # warning #1
    clock["t"] += _GTFS_ONLY_WARN_INTERVAL_S - 10
    server._note_stop_data_source(
        SID, oba_realtime_count=0, produced=2
    )  # still suppressed
    assert len(_warnings(records)) == 1
    clock["t"] += 20  # now past the re-warn interval
    server._note_stop_data_source(SID, oba_realtime_count=0, produced=2)  # warning #2
    assert len(_warnings(records)) == 2
