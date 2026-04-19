"""Firmware reconnect-storm invariant: no broadcast trip may have a
``departureTime`` that is already more than 60 seconds in the past.

The ESP32 firmware (components/transit_tracker/transit_tracker.cpp L26-53)
runs a ``check_stale_trips`` interval every 10 seconds. When every trip in
the current schedule has ``now - departure_time > 60``, the firmware calls
``reconnect()``. A proxy that keeps broadcasting the same stale payloads
therefore drives the device into a reconnect storm.

The reference cloud proxy avoids this by filtering ``trip[sortKey] > now``
AFTER applying the user-configured time offset — so every broadcast trip's
``departureTime`` is strictly in the future (see tjhorner/transit-tracker-api,
src/schedule/schedule.service.ts L91). This test codifies that invariant
against our local ``TransitServer`` using a pathological case that reproduces
the reported reconnect storm: a bus whose raw arrival is within a negative
walking-time offset window.
"""

from __future__ import annotations

import time

import pytest

from transit_tracker.config import TransitConfig
from transit_tracker.network.websocket_server import TransitServer

from conftest import MockWS

pytestmark = pytest.mark.contract


def _build_server() -> TransitServer:
    config = TransitConfig()
    return TransitServer(config)


@pytest.mark.asyncio
async def test_negative_offset_inside_walk_window_is_filtered_out():
    """A bus arriving in 5 min with a -7 min walk offset must NOT be broadcast.

    With offset=-420s applied, the displayed arrivalTime lands 2 min in the past.
    The firmware's stale check would see this as a stale trip (>60s past) and,
    if it is the only trip, reconnect. Cloud proxy filters these with
    ``trip[sortKey] > now`` so they never ship. Local must match.
    """
    now = int(time.time())
    server = _build_server()
    ws = MockWS()

    server.subscriptions[ws] = [
        {"routeId": "st:40_100240", "stopId": "st:1_8494", "offset": -420}
    ]
    server.client_limits[ws] = 5

    server.cache["1_8494"] = (time.time(), [{
        "tripId": "inside_walk_window",
        "routeId": "40_100240",
        "predictedArrivalTime": (now + 300) * 1000,   # 5 min out
        "predictedDepartureTime": (now + 305) * 1000,
        "routeName": "554",
        "headsign": "Seattle",
        "isRealtime": True,
        "arrivalEnabled": True,
        "departureEnabled": True,
    }])

    await server.send_update(ws)

    trips = ws.sent[0]["data"]["trips"]
    for t in trips:
        assert t["departureTime"] > now - 60, (
            f"Trip {t['tripId']} has departureTime {t['departureTime'] - now}s "
            "relative to now — firmware stale-check would trigger reconnect."
        )


@pytest.mark.asyncio
async def test_broadcast_is_empty_when_all_trips_fall_inside_walk_window():
    """When EVERY upcoming trip lands in the past after offset, the proxy must
    emit an empty trips array (cloud behavior). Firmware treats empty trips as
    'No upcoming arrivals' and does NOT reconnect — only all-stale triggers it.
    """
    now = int(time.time())
    server = _build_server()
    ws = MockWS()

    server.subscriptions[ws] = [
        {"routeId": "st:1_100039", "stopId": "st:1_11920", "offset": -540}
    ]
    server.client_limits[ws] = 5

    # Every trip in OBA's window arrives within the 9-minute walk offset.
    server.cache["1_11920"] = (time.time(), [
        {
            "tripId": f"inside_walk_{i}",
            "routeId": "1_100039",
            "predictedArrivalTime": (now + 60 + i * 30) * 1000,  # 1 min, 1.5 min, 2 min
            "predictedDepartureTime": (now + 65 + i * 30) * 1000,
            "routeName": "14",
            "headsign": "Mount Baker",
            "isRealtime": True,
            "arrivalEnabled": True,
            "departureEnabled": True,
        }
        for i in range(3)
    ])

    await server.send_update(ws)

    trips = ws.sent[0]["data"]["trips"]
    # Either no trips (cloud-equivalent) OR every trip is still in the future.
    # Any trip broadcast with past departureTime drives the reconnect storm.
    for t in trips:
        assert t["departureTime"] > now, (
            f"Trip {t['tripId']} broadcast with past departureTime "
            f"({t['departureTime'] - now}s) — all-stale condition triggers "
            "firmware reconnect."
        )


@pytest.mark.asyncio
async def test_future_trips_retained_unchanged():
    """Regression: the filter fix must not drop trips that the cloud would keep."""
    now = int(time.time())
    server = _build_server()
    ws = MockWS()

    server.subscriptions[ws] = [
        {"routeId": "st:40_100240", "stopId": "st:1_8494", "offset": -420}
    ]
    server.client_limits[ws] = 5

    # Bus 15 min out with -7 min offset → displayed at +8 min from now. Must ship.
    server.cache["1_8494"] = (time.time(), [{
        "tripId": "outside_walk_window",
        "routeId": "40_100240",
        "predictedArrivalTime": (now + 900) * 1000,
        "predictedDepartureTime": (now + 905) * 1000,
        "routeName": "554",
        "headsign": "Seattle",
        "isRealtime": True,
        "arrivalEnabled": True,
        "departureEnabled": True,
    }])

    await server.send_update(ws)

    trips = ws.sent[0]["data"]["trips"]
    assert len(trips) == 1
    assert trips[0]["tripId"] == "outside_walk_window"
    # +15min - 7min = +8min displayed; well clear of the stale threshold.
    assert trips[0]["arrivalTime"] == now + 900 - 420
    assert trips[0]["departureTime"] == now + 905 - 420
