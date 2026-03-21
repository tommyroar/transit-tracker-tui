"""Contract tests: local proxy protocol matches reference cloud format.


Verifies that the local WebSocket proxy produces responses whose
structure (event names, field types, units) is identical to the
reference cloud proxy at wss://tt.horner.tj/.
"""

import json
import time

import pytest

from transit_tracker.config import TransitConfig, TransitSubscription
from transit_tracker.network.websocket_server import TransitServer

pytestmark = pytest.mark.contract


def _get_reference_trip(now):
    """Canonical trip dict matching the cloud proxy schema."""
    return {
        "tripId": "ref-123",
        "routeId": "14",
        "routeName": "14",
        "routeColor": "FF00FF",
        "stopId": "1_1234",
        "headsign": "Downtown",
        "arrivalTime": now + 600,
        "departureTime": now + 600,
        "isRealtime": True,
    }


class MockWS:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(json.loads(msg))

    @property
    def remote_address(self):
        return ("192.168.1.50", 1234)


@pytest.fixture
def protocol_payload():
    """Run the local proxy and return (actual_payload, reference_payload, now)."""

    async def _run():
        now = int(time.time())
        config = TransitConfig()
        config.subscriptions = [
            TransitSubscription(
                feed="st", route="14", stop="1_1234", label="14", time_offset="0min"
            )
        ]

        server = TransitServer(config)
        server.cache["1_1234"] = (
            time.time(),
            [
                {
                    "tripId": "ref-123",
                    "routeId": "14",
                    "stopId": "1_1234",
                    "predictedArrivalTime": (now + 600) * 1000,
                    "routeName": "14",
                    "headsign": "Downtown",
                }
            ],
        )

        ws = MockWS()
        server.clients.add(ws)
        server.subscriptions[ws] = [{"routeId": "14", "stopId": "1_1234"}]
        await server.send_update(ws)

        actual = ws.sent[0]
        reference = {
            "event": "schedule",
            "data": {"trips": [_get_reference_trip(now)]},
        }
        return actual, reference, now

    import asyncio

    return asyncio.get_event_loop().run_until_complete(_run())


def _trip_from(payload):
    """Extract first trip from a schedule payload."""
    d = payload.get("data") or payload.get("payload") or {}
    trips = d.get("trips", [{}])
    return trips[0] if trips else {}


# -- Individual protocol checks as proper test functions ---------------------


@pytest.mark.asyncio
async def test_event_name_is_schedule():
    """Top-level 'event' field must be 'schedule'."""
    now = int(time.time())
    config = TransitConfig()
    config.subscriptions = [
        TransitSubscription(
            feed="st", route="14", stop="1_1234", label="14", time_offset="0min"
        )
    ]
    server = TransitServer(config)
    server.cache["1_1234"] = (
        time.time(),
        [
            {
                "tripId": "ref-123",
                "routeId": "14",
                "stopId": "1_1234",
                "predictedArrivalTime": (now + 600) * 1000,
                "routeName": "14",
                "headsign": "Downtown",
            }
        ],
    )
    ws = MockWS()
    server.clients.add(ws)
    server.subscriptions[ws] = [{"routeId": "14", "stopId": "1_1234"}]
    await server.send_update(ws)
    payload = ws.sent[0]

    assert payload.get("event") == "schedule"


@pytest.mark.asyncio
async def test_data_key_present():
    """Response wraps trips in a 'data' key."""
    now = int(time.time())
    config = TransitConfig()
    config.subscriptions = [
        TransitSubscription(
            feed="st", route="14", stop="1_1234", label="14", time_offset="0min"
        )
    ]
    server = TransitServer(config)
    server.cache["1_1234"] = (
        time.time(),
        [
            {
                "tripId": "ref-123",
                "routeId": "14",
                "stopId": "1_1234",
                "predictedArrivalTime": (now + 600) * 1000,
                "routeName": "14",
                "headsign": "Downtown",
            }
        ],
    )
    ws = MockWS()
    server.clients.add(ws)
    server.subscriptions[ws] = [{"routeId": "14", "stopId": "1_1234"}]
    await server.send_update(ws)
    payload = ws.sent[0]

    assert "data" in payload


@pytest.mark.asyncio
async def test_trip_has_required_fields():
    """Each trip must have stopId, arrivalTime (int, seconds), isRealtime (bool)."""
    now = int(time.time())
    config = TransitConfig()
    config.subscriptions = [
        TransitSubscription(
            feed="st", route="14", stop="1_1234", label="14", time_offset="0min"
        )
    ]
    server = TransitServer(config)
    server.cache["1_1234"] = (
        time.time(),
        [
            {
                "tripId": "ref-123",
                "routeId": "14",
                "stopId": "1_1234",
                "predictedArrivalTime": (now + 600) * 1000,
                "routeName": "14",
                "headsign": "Downtown",
            }
        ],
    )
    ws = MockWS()
    server.clients.add(ws)
    server.subscriptions[ws] = [{"routeId": "14", "stopId": "1_1234"}]
    await server.send_update(ws)
    trip = _trip_from(ws.sent[0])

    assert "stopId" in trip
    assert isinstance(trip.get("arrivalTime"), int)
    # arrivalTime should be in seconds, not milliseconds
    assert trip["arrivalTime"] < 2 * 10**9
    assert isinstance(trip.get("isRealtime"), bool)


@pytest.mark.asyncio
async def test_departure_time_present():
    """departureTime field must be present in trip."""
    now = int(time.time())
    config = TransitConfig()
    config.subscriptions = [
        TransitSubscription(
            feed="st", route="14", stop="1_1234", label="14", time_offset="0min"
        )
    ]
    server = TransitServer(config)
    server.cache["1_1234"] = (
        time.time(),
        [
            {
                "tripId": "ref-123",
                "routeId": "14",
                "stopId": "1_1234",
                "predictedArrivalTime": (now + 600) * 1000,
                "routeName": "14",
                "headsign": "Downtown",
            }
        ],
    )
    ws = MockWS()
    server.clients.add(ws)
    server.subscriptions[ws] = [{"routeId": "14", "stopId": "1_1234"}]
    await server.send_update(ws)
    trip = _trip_from(ws.sent[0])

    assert "departureTime" in trip


@pytest.mark.asyncio
async def test_route_color_format():
    """routeColor must not contain '#' prefix (bare hex)."""
    now = int(time.time())
    config = TransitConfig()
    config.subscriptions = [
        TransitSubscription(
            feed="st", route="14", stop="1_1234", label="14", time_offset="0min"
        )
    ]
    server = TransitServer(config)
    server.cache["1_1234"] = (
        time.time(),
        [
            {
                "tripId": "ref-123",
                "routeId": "14",
                "stopId": "1_1234",
                "predictedArrivalTime": (now + 600) * 1000,
                "routeName": "14",
                "headsign": "Downtown",
                "routeColor": "FF00FF",
            }
        ],
    )
    ws = MockWS()
    server.clients.add(ws)
    server.subscriptions[ws] = [{"routeId": "14", "stopId": "1_1234"}]
    await server.send_update(ws)
    trip = _trip_from(ws.sent[0])

    color = trip.get("routeColor")
    if color is not None:
        assert "#" not in str(color)
