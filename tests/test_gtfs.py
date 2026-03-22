"""

Tests for GTFSSchedule and its integration with TransitServer.
Uses in-memory SQLite databases — no real GTFS files required.
"""

import json
import sqlite3
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from transit_tracker.gtfs_schedule import GTFSSchedule
from transit_tracker.network.websocket_server import TransitServer

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_gtfs_db(tmp_path, *, services=None, exceptions=None, routes=None, departures=None):
    """Build a minimal GTFS SQLite database for testing."""
    db_path = tmp_path / "test_gtfs.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE stop_departures (
            stop_id TEXT NOT NULL, departure_sec INTEGER NOT NULL,
            service_id TEXT NOT NULL, trip_id TEXT NOT NULL,
            route_id TEXT NOT NULL, headsign TEXT, direction_id INTEGER
        );
        CREATE TABLE services (
            service_id TEXT PRIMARY KEY,
            monday INT, tuesday INT, wednesday INT, thursday INT,
            friday INT, saturday INT, sunday INT,
            start_date TEXT, end_date TEXT
        );
        CREATE TABLE service_exceptions (
            service_id TEXT NOT NULL, date TEXT NOT NULL,
            exception_type INTEGER NOT NULL, PRIMARY KEY (service_id, date)
        );
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY, short_name TEXT,
            long_name TEXT, color TEXT, route_type INTEGER
        );
        CREATE INDEX idx_stop_dep ON stop_departures (stop_id, departure_sec);
    """)

    if services:
        conn.executemany(
            "INSERT INTO services VALUES (?,?,?,?,?,?,?,?,?,?)", services
        )
    if exceptions:
        conn.executemany(
            "INSERT INTO service_exceptions VALUES (?,?,?)", exceptions
        )
    if routes:
        conn.executemany("INSERT INTO routes VALUES (?,?,?,?,?)", routes)
    if departures:
        conn.executemany(
            "INSERT INTO stop_departures VALUES (?,?,?,?,?,?,?)", departures
        )

    conn.commit()
    conn.close()
    return str(db_path)


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.subscriptions = []
    config.service = MagicMock()
    config.service.use_local_api = True
    config.service.arrival_threshold_minutes = 5
    config.service.check_interval_seconds = 30
    config.service.request_spacing_ms = 250
    config.service.oba_api_key = None
    config.transit_tracker = MagicMock()
    config.transit_tracker.abbreviations = []
    config.transit_tracker.time_display = "arrival"
    return config


# ---------------------------------------------------------------------------
# Unit tests: GTFSSchedule
# ---------------------------------------------------------------------------


def test_is_available_false_when_no_db(tmp_path):
    gtfs = GTFSSchedule(db_path=str(tmp_path / "nonexistent.sqlite"))
    assert not gtfs.is_available()


def test_is_available_true_when_db_exists(tmp_path):
    db_path = make_gtfs_db(tmp_path)
    gtfs = GTFSSchedule(db_path=db_path)
    assert gtfs.is_available()


def test_strip_agency_prefix():
    assert GTFSSchedule._strip_agency_prefix("95_7") == "7"
    assert GTFSSchedule._strip_agency_prefix("1_12345") == "12345"
    assert GTFSSchedule._strip_agency_prefix("40_100479") == "100479"
    assert GTFSSchedule._strip_agency_prefix("WSF028") == "WSF028"
    assert GTFSSchedule._strip_agency_prefix("") == ""
    assert GTFSSchedule._strip_agency_prefix("95_WSF028") == "WSF028"


def test_get_active_service_ids_weekday(tmp_path):
    """Service active on weekdays should be returned on a Wednesday."""
    import datetime

    db_path = make_gtfs_db(
        tmp_path,
        services=[
            # service_id, mon..sun, start_date, end_date
            ("weekday_svc", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231"),
            ("weekend_svc", 0, 0, 0, 0, 0, 1, 1, "20260101", "20261231"),
        ],
    )
    gtfs = GTFSSchedule(db_path=db_path)
    wednesday = datetime.date(2026, 3, 18)  # a Wednesday
    active = gtfs.get_active_service_ids(wednesday)
    assert "weekday_svc" in active
    assert "weekend_svc" not in active


def test_get_active_service_ids_exception_removes(tmp_path):
    """calendar_dates exception_type=2 should remove a service for that date."""
    import datetime

    db_path = make_gtfs_db(
        tmp_path,
        services=[
            ("weekday_svc", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231"),
        ],
        exceptions=[
            # Remove weekday_svc on 2026-03-17 (a Tuesday holiday)
            ("weekday_svc", "20260317", 2),
        ],
    )
    gtfs = GTFSSchedule(db_path=db_path)
    holiday = datetime.date(2026, 3, 17)
    active = gtfs.get_active_service_ids(holiday)
    assert "weekday_svc" not in active


def test_get_active_service_ids_exception_adds(tmp_path):
    """calendar_dates exception_type=1 should add a service on that date."""
    import datetime

    db_path = make_gtfs_db(
        tmp_path,
        services=[
            ("weekday_svc", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231"),
        ],
        exceptions=[
            # Add a special weekend service on a weekday
            ("extra_svc", "20260317", 1),
        ],
    )
    gtfs = GTFSSchedule(db_path=db_path)
    date = datetime.date(2026, 3, 17)
    active = gtfs.get_active_service_ids(date)
    assert "extra_svc" in active
    assert "weekday_svc" in active  # still active from calendar


def test_get_next_departures_basic(tmp_path):
    """Should return upcoming scheduled trips sorted by departure time."""
    import datetime

    # Tuesday March 17 2026, 08:00 AM local
    # Use a fixed "now" by monkeypatching via a known time
    tuesday = datetime.date(2026, 3, 17)
    midnight = datetime.datetime.combine(tuesday, datetime.time.min).timestamp()
    now = midnight + 8 * 3600  # 08:00 AM

    db_path = make_gtfs_db(
        tmp_path,
        services=[
            ("weekday", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231"),
        ],
        routes=[
            ("100", "Route 100", "Test Route", "FF0000", 3),
        ],
        departures=[
            # stop_id, dep_sec, service_id, trip_id, route_id, headsign, dir
            ("12345", 9 * 3600, "weekday", "trip1", "100", "Downtown", 0),  # 9 AM
            ("12345", 10 * 3600, "weekday", "trip2", "100", "Airport", 0),  # 10 AM
            ("12345", 7 * 3600, "weekday", "trip0", "100", "Past", 0),  # 7 AM (past)
        ],
    )
    gtfs = GTFSSchedule(db_path=db_path)
    # count=2 so we only retrieve today's 2 upcoming trips (not future days)
    trips = gtfs.get_next_departures("12345", set(), now, count=2)

    assert len(trips) == 2
    assert trips[0]["tripId"] == "trip1"
    assert trips[0]["headsign"] == "Downtown"
    assert trips[0]["routeName"] == "Route 100"
    assert trips[0]["routeColor"] == "FF0000"
    assert trips[0]["isRealtime"] is False
    # Should be after 8 AM
    assert trips[0]["arrivalTime"] >= int(now)


def test_get_next_departures_route_filter(tmp_path):
    """Should only return trips for matching route_ids when filter provided."""
    import datetime

    tuesday = datetime.date(2026, 3, 17)
    midnight = datetime.datetime.combine(tuesday, datetime.time.min).timestamp()
    now = midnight + 8 * 3600

    db_path = make_gtfs_db(
        tmp_path,
        services=[("weekday", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231")],
        routes=[
            ("100", "100", None, None, 3),
            ("200", "200", None, None, 3),
        ],
        departures=[
            ("12345", 9 * 3600, "weekday", "trip1", "100", "Downtown", 0),
            ("12345", 9 * 3600 + 30, "weekday", "trip2", "200", "Airport", 0),
        ],
    )
    gtfs = GTFSSchedule(db_path=db_path)

    # Filter to route 100 only (passed as OBA prefixed ID to test normalization)
    # count=1 so we only get the first upcoming trip for today
    trips = gtfs.get_next_departures("12345", {"1_100"}, now, count=1)
    assert len(trips) == 1
    assert trips[0]["routeId"] == "100"


def test_get_next_departures_agency_prefix_stripped(tmp_path):
    """Should work when stop_id is passed with agency prefix (e.g., '95_7')."""
    import datetime

    tuesday = datetime.date(2026, 3, 17)
    midnight = datetime.datetime.combine(tuesday, datetime.time.min).timestamp()
    now = midnight + 8 * 3600

    db_path = make_gtfs_db(
        tmp_path,
        services=[("weekday", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231")],
        routes=[("WSF028", "WSF", None, None, 4)],
        departures=[
            # GTFS stores bare stop ID "7", but caller passes "95_7"
            ("7", 9 * 3600, "weekday", "ferry1", "WSF028", "Bainbridge", 0),
        ],
    )
    gtfs = GTFSSchedule(db_path=db_path)
    # count=1 to limit to today's single trip
    trips = gtfs.get_next_departures("95_7", set(), now, count=1)
    assert len(trips) == 1
    assert trips[0]["tripId"] == "ferry1"


def test_get_next_departures_returns_empty_when_no_db(tmp_path):
    gtfs = GTFSSchedule(db_path=str(tmp_path / "missing.sqlite"))
    trips = gtfs.get_next_departures("12345", set(), time.time(), count=5)
    assert trips == []


def test_get_next_departures_wraps_to_tomorrow(tmp_path):
    """When near midnight, should find trips for the next day."""
    import datetime

    # Tuesday 11:55 PM
    tuesday = datetime.date(2026, 3, 17)
    midnight = datetime.datetime.combine(tuesday, datetime.time.min).timestamp()
    now = midnight + 23 * 3600 + 55 * 60  # 23:55

    wednesday = datetime.date(2026, 3, 18)

    db_path = make_gtfs_db(
        tmp_path,
        services=[
            ("tuesday_svc", 0, 1, 0, 0, 0, 0, 0, "20260101", "20261231"),
            ("wednesday_svc", 0, 0, 1, 0, 0, 0, 0, "20260101", "20261231"),
        ],
        routes=[("100", "100", None, None, 3)],
        departures=[
            # Tuesday: no trips after 23:55 PM
            ("12345", 22 * 3600, "tuesday_svc", "tues1", "100", "Past", 0),
            # Wednesday early AM
            ("12345", 1 * 3600, "wednesday_svc", "wed1", "100", "Early", 0),
        ],
    )
    gtfs = GTFSSchedule(db_path=db_path)
    trips = gtfs.get_next_departures("12345", set(), now, count=5)

    # Should find Wednesday's 1 AM trip
    assert any(t["tripId"] == "wed1" for t in trips)


# ---------------------------------------------------------------------------
# Integration tests: TransitServer + GTFS
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wake_up_sends_gtfs_on_cache_miss(tmp_path, mock_config):
    """When cache is empty, send_update should return GTFS scheduled trips."""
    import datetime

    tuesday = datetime.date(2026, 3, 17)
    midnight = datetime.datetime.combine(tuesday, datetime.time.min).timestamp()
    now_ts = midnight + 8 * 3600

    db_path = make_gtfs_db(
        tmp_path,
        services=[("weekday", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231")],
        routes=[("100", "100", None, None, 3)],
        departures=[
            ("12345", int(8 * 3600 + 600), "weekday", "trip1", "100", "Downtown", 0),
        ],
    )

    server = TransitServer(mock_config)
    server.gtfs = GTFSSchedule(db_path=db_path)
    # Cache is empty — simulates cold start / wake-up

    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.remote_address = ("192.168.1.100", 12345)

    # Subscribe to stop 12345 (OBA format: "1_12345")
    server.subscriptions[ws] = [{"routeId": "1_100", "stopId": "1_12345", "offset": 0}]

    # Monkeypatch time.time so GTFS uses our fixed now
    import unittest.mock

    with unittest.mock.patch("transit_tracker.network.websocket_server.time") as mock_time:
        mock_time.time.return_value = now_ts
        await server.send_update(ws)

    ws.send.assert_called_once()
    payload = json.loads(ws.send.call_args[0][0])
    trips = payload["data"]["trips"]
    assert len(trips) > 0
    assert trips[0]["isRealtime"] is False


@pytest.mark.asyncio
async def test_ferry_fallback_when_no_live_data(tmp_path, mock_config):
    """Ferry stop with no live OBA data should use GTFS scheduled departures."""
    import datetime

    tuesday = datetime.date(2026, 3, 17)
    midnight = datetime.datetime.combine(tuesday, datetime.time.min).timestamp()
    now_ts = midnight + 8 * 3600

    db_path = make_gtfs_db(
        tmp_path,
        services=[("weekday", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231")],
        routes=[("WSF028", "Seattle-BI", None, None, 4)],
        departures=[
            ("7", int(8 * 3600 + 600), "weekday", "ferry1", "WSF028", "Bainbridge", 0),
        ],
    )

    server = TransitServer(mock_config)
    server.gtfs = GTFSSchedule(db_path=db_path)

    # Populate cache with non-realtime (scheduled-only) OBA arrivals for the ferry stop
    import time as real_time

    server.cache["95_7"] = (
        real_time.time(),
        [
            {
                "tripId": "oba_trip",
                "routeId": "95_WSF028",
                "arrivalTime": int(now_ts + 900),
                "departureTime": int(now_ts + 900),
                "scheduledArrivalTime": int((now_ts + 900) * 1000),
                "scheduledDepartureTime": int((now_ts + 900) * 1000),
                "predictedArrivalTime": None,
                "predictedDepartureTime": None,
                "isRealtime": False,
                "vehicleId": None,
                "headsign": "Bainbridge Island",
                "routeName": "WSF",
                "departureEnabled": True,
                "arrivalEnabled": True,  # both enabled so direction filter passes
            }
        ],
    )

    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.remote_address = ("192.168.1.100", 12345)
    server.subscriptions[ws] = [
        {"routeId": "95_WSF028", "stopId": "wsf:7", "offset": 0}
    ]

    import unittest.mock

    with unittest.mock.patch("transit_tracker.network.websocket_server.time") as mock_time:
        mock_time.time.return_value = now_ts
        await server.send_update(ws)

    ws.send.assert_called_once()
    payload = json.loads(ws.send.call_args[0][0])
    trips = payload["data"]["trips"]
    # Should have trips (either OBA scheduled or GTFS fallback)
    assert len(trips) > 0


@pytest.mark.asyncio
async def test_ferry_fallback_not_added_when_live_data_exists(tmp_path, mock_config):
    """When ferry has live (realtime) OBA data, GTFS should NOT duplicate trips."""
    import datetime

    tuesday = datetime.date(2026, 3, 17)
    midnight = datetime.datetime.combine(tuesday, datetime.time.min).timestamp()
    now_ts = midnight + 8 * 3600

    db_path = make_gtfs_db(
        tmp_path,
        services=[("weekday", 1, 1, 1, 1, 1, 0, 0, "20260101", "20261231")],
        routes=[("WSF028", "Seattle-BI", None, None, 4)],
        departures=[
            ("7", int(8 * 3600 + 600), "weekday", "gtfs_ferry", "WSF028", "Bainbridge", 0),
        ],
    )

    server = TransitServer(mock_config)
    server.gtfs = GTFSSchedule(db_path=db_path)

    # Live realtime OBA data exists for this ferry stop
    import time as real_time

    server.cache["95_7"] = (
        real_time.time(),
        [
            {
                "tripId": "live_ferry_trip",
                "routeId": "95_WSF028",
                "arrivalTime": int(now_ts + 600),
                "departureTime": int(now_ts + 600),
                "scheduledArrivalTime": int((now_ts + 600) * 1000),
                "scheduledDepartureTime": int((now_ts + 600) * 1000),
                "predictedArrivalTime": int((now_ts + 600) * 1000),
                "predictedDepartureTime": int((now_ts + 600) * 1000),
                "isRealtime": True,
                "vehicleId": "95_28",  # Live vehicle = Sealth
                "headsign": "Bainbridge Island",
                "routeName": "WSF",
                "departureEnabled": True,
                "arrivalEnabled": True,  # both enabled so direction filter passes
            }
        ],
    )

    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.remote_address = ("192.168.1.100", 12345)
    server.client_limits[ws] = 3
    server.subscriptions[ws] = [
        {"routeId": "95_WSF028", "stopId": "wsf:7", "offset": 0}
    ]

    import unittest.mock
    with unittest.mock.patch("transit_tracker.network.websocket_server.time") as mock_time:
        mock_time.time.return_value = now_ts
        await server.send_update(ws)

    ws.send.assert_called_once()
    payload = json.loads(ws.send.call_args[0][0])
    trips = payload["data"]["trips"]

    # Live trip should be present
    trip_ids = [t["tripId"] for t in trips]
    assert "live_ferry_trip" in trip_ids

    # No GTFS duplicates (should not have gtfs_ferry alongside live trip)
    gtfs_trips = [t for t in trips if t.get("isRealtime") is False]
    # When live data exists, GTFS fallback should not trigger
    assert len(gtfs_trips) == 0
