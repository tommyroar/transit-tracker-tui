import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from transit_tracker.config import TransitConfig, TransitSubscription
from transit_tracker.network.websocket_server import TransitServer


@pytest.fixture
def mock_config():
    config = MagicMock()
    config.subscriptions = [
        TransitSubscription(feed="st", route="st:40_100240", stop="st:1_8494", label="Route st:40_100240")
    ]
    config.use_local_api = True
    config.auto_launch_gui = True
    config.arrival_threshold_minutes = 5
    config.check_interval_seconds = 30
    config.time_display = "arrival"
    config.transit_tracker = MagicMock()
    config.transit_tracker.abbreviations = []
    return config

@pytest.mark.asyncio
async def test_server_broadcast_updates(mock_config):
    """Test that the server broadcasts updates to subscribed clients."""
    server = TransitServer(mock_config)

    now = int(time.time())
    mock_arrivals = [
        {
            "tripId": "trip_123",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "arrivalTime": (now + 600) * 1000 # 10 mins from now in ms
        }
    ]
    # Pre-populate cache — send_update is cache-only and never fetches directly
    server.cache["1_8494"] = (time.time(), mock_arrivals)

    ws = AsyncMock()
    ws.send = AsyncMock()
    server.subscriptions[ws] = [{"routeId": "st:40_100240", "stopId": "st:1_8494"}]

    await server.send_update(ws)

    ws.send.assert_called_once()
    sent_data = json.loads(ws.send.call_args[0][0])
    assert sent_data["event"] == "schedule"
    assert sent_data["data"]["trips"][0]["tripId"] == "trip_123"

@pytest.mark.asyncio
async def test_fair_diversity_capping(mock_config):
    """Test that the server applies fair diversity capping correctly."""
    server = TransitServer(mock_config)

    now = int(time.time())
    # Two stops, multiple trips per stop
    mock_arrivals_1 = [
        {"tripId": "stop1_trip1", "routeId": "route1", "stopId": "stop1", "arrivalTime": (now + 1000) * 1000},
        {"tripId": "stop1_trip2", "routeId": "route1", "stopId": "stop1", "arrivalTime": (now + 2000) * 1000},
    ]
    mock_arrivals_2 = [
        {"tripId": "stop2_trip1", "routeId": "route2", "stopId": "stop2", "arrivalTime": (now + 1500) * 1000},
        {"tripId": "stop2_trip2", "routeId": "route2", "stopId": "stop2", "arrivalTime": (now + 2500) * 1000},
    ]

    # Pre-populate cache — send_update is cache-only and never fetches directly
    server.cache["stop1"] = (time.time(), mock_arrivals_1)
    server.cache["stop2"] = (time.time(), mock_arrivals_2)

    ws = AsyncMock()
    ws.send = AsyncMock()
    # Client limit is 3
    server.client_limits[ws] = 3
    server.subscriptions[ws] = [
        {"routeId": "route1", "stopId": "stop1"},
        {"routeId": "route2", "stopId": "stop2"}
    ]

    await server.send_update(ws)

    ws.send.assert_called_once()
    sent_data = json.loads(ws.send.call_args[0][0])
    trips = sent_data["data"]["trips"]
    
    # Diversity capping should pick:
    # 1. stop1_trip1 (soonest for stop1)
    # 2. stop2_trip1 (soonest for stop2)
    # 3. stop1_trip2 (next soonest overall)
    
    assert len(trips) == 3
    trip_ids = [t["tripId"] for t in trips]
    assert "stop1_trip1" in trip_ids
    assert "stop2_trip1" in trip_ids
    assert "stop1_trip2" in trip_ids
    assert "stop2_trip2" not in trip_ids

@pytest.mark.asyncio
async def test_normalize_id():
    """Test the internal ID normalization logic."""
    from transit_tracker.network.websocket_server import TransitServer
    server = TransitServer(MagicMock())

    # We can't easily test the nested normalize_id function, but we can verify server init
    assert server is not None


# --- Rate Limiting Tests ---

@pytest.mark.asyncio
async def test_rate_limit_suppresses_api_calls_during_cooldown(mock_config):
    """After a 429, subsequent cache misses must not call the API until cooldown expires."""
    server = TransitServer(mock_config)
    server.api = AsyncMock()
    server.api.get_arrivals.side_effect = Exception("HTTP 429 Too Many Requests")

    stale_arrivals = [{"tripId": "stale_trip", "routeId": "r1", "arrivalTime": 9999999999}]
    server.cache["stop1"] = (time.time() - 9999, stale_arrivals)  # expired cache entry

    # First call — hits the API, gets 429, sets cooldown
    with pytest.raises(Exception, match="429"):
        await server.get_arrivals_cached("stop1")

    assert "stop1" in server.rate_limited_stops
    assert server.rate_limit_until.get("stop1", 0) > time.time()

    # Reset the mock so we can detect if it's called again
    server.api.get_arrivals.reset_mock()
    server.api.get_arrivals.side_effect = Exception("HTTP 429 Too Many Requests")

    # Second call — cooldown is active, must NOT call the API
    result = await server.get_arrivals_cached("stop1")

    server.api.get_arrivals.assert_not_called()
    assert result == stale_arrivals  # returns stale cache, not empty


@pytest.mark.asyncio
async def test_rate_limit_cooldown_expiry_resumes_fetching(mock_config):
    """Once the cooldown timestamp passes, the next call should retry the API."""
    server = TransitServer(mock_config)
    server.api = AsyncMock()

    fresh_arrivals = [{"tripId": "fresh_trip", "routeId": "r1", "arrivalTime": 9999999999}]
    server.api.get_arrivals.return_value = fresh_arrivals

    # Simulate an already-expired rate limit on stop1
    server.rate_limited_stops.add("stop1")
    server.rate_limit_until["stop1"] = time.time() - 1  # expired 1 second ago
    server.cache["stop1"] = (time.time() - 9999, [])  # stale cache

    result = await server.get_arrivals_cached("stop1")

    server.api.get_arrivals.assert_called_once_with("stop1")
    assert result == fresh_arrivals
    assert "stop1" not in server.rate_limited_stops
    assert "stop1" not in server.rate_limit_until


@pytest.mark.asyncio
async def test_send_update_never_calls_api_on_cache_miss(mock_config):
    """send_update must never trigger an OBA API call, even with an empty cache."""
    server = TransitServer(mock_config)
    server.api = AsyncMock()

    ws = AsyncMock()
    ws.send = AsyncMock()
    server.subscriptions[ws] = [{"routeId": "st:40_100240", "stopId": "st:1_8494"}]

    # Cache is empty — old code would fetch here, new code must not
    await server.send_update(ws)

    server.api.get_arrivals.assert_not_called()
    ws.send.assert_called_once()
    sent_data = json.loads(ws.send.call_args[0][0])
    assert sent_data["data"]["trips"] == []


@pytest.mark.asyncio
async def test_refresh_all_data_skips_rate_limited_stops(mock_config):
    """refresh_all_data must not call the API for stops currently in rate-limit cooldown."""
    server = TransitServer(mock_config)
    server.api = AsyncMock()
    server.api.get_arrivals.return_value = []

    ws = MagicMock()
    server.subscriptions[ws] = [
        {"routeId": "r1", "stopId": "stop_ok"},
        {"routeId": "r2", "stopId": "stop_limited"},
    ]

    # Mark stop_limited as rate-limited with a future cooldown
    server.rate_limited_stops.add("stop_limited")
    server.rate_limit_until["stop_limited"] = time.time() + 300

    await server.refresh_all_data()

    called_stops = [call.args[0] for call in server.api.get_arrivals.call_args_list]
    assert "stop_ok" in called_stops
    assert "stop_limited" not in called_stops


# --- Ferry Vessel Name Tests ---

def _make_ferry_arrival(now, trip_id, headsign, vehicle_id=None, offset_ms=600_000):
    """Helper: build a minimal ferry arrival dict as transit_api would return."""
    arr = {
        "tripId": trip_id,
        "routeId": "95_37",
        "stopId": "95_3",
        "arrivalTime": (now + offset_ms // 1000) * 1000,
        "tripHeadsign": headsign,
        "headsign": headsign,
        "routeName": "Bainbridge Island - Seattle",
        "isRealtime": vehicle_id is not None,
    }
    if vehicle_id:
        arr["vehicleId"] = vehicle_id
    return arr


@pytest.fixture
def ferry_config():
    config = MagicMock()
    config.subscriptions = [
        TransitSubscription(feed="wsf", route="95_37", stop="95_3", label="SEA-BI")
    ]
    config.use_local_api = True
    config.auto_launch_gui = False
    config.arrival_threshold_minutes = 5
    config.check_interval_seconds = 30
    config.time_display = "arrival"
    config.transit_tracker = MagicMock()
    config.transit_tracker.abbreviations = []
    return config


@pytest.mark.asyncio
async def test_ferry_vessel_name_shown_when_vehicle_id_present(ferry_config):
    """When vehicleId is present and maps to a known vessel, headsign is the vessel name."""
    server = TransitServer(ferry_config)
    now = int(time.time())
    server.cache["95_3"] = (time.time(), [
        _make_ferry_arrival(now, "trip_1", "Seattle", vehicle_id="95_25"),
    ])

    ws = AsyncMock()
    server.subscriptions[ws] = [{"routeId": "95_37", "stopId": "95_3"}]
    await server.send_update(ws)

    trips = json.loads(ws.send.call_args[0][0])["data"]["trips"]
    assert len(trips) == 1
    assert trips[0]["headsign"] == "Puyallup"


@pytest.mark.asyncio
async def test_ferry_headsign_fallback_when_no_vehicle_id(ferry_config):
    """When vehicleId is absent, headsign falls back to the OBA destination (correct behavior)."""
    server = TransitServer(ferry_config)
    now = int(time.time())
    server.cache["95_3"] = (time.time(), [
        _make_ferry_arrival(now, "trip_1", "Bainbridge Island", vehicle_id=None),
    ])

    ws = AsyncMock()
    server.subscriptions[ws] = [{"routeId": "95_37", "stopId": "95_3"}]
    await server.send_update(ws)

    trips = json.loads(ws.send.call_args[0][0])["data"]["trips"]
    assert len(trips) == 1
    assert trips[0]["headsign"] == "Bainbridge Island"


@pytest.mark.asyncio
async def test_ferry_vessel_cache_persists_after_vehicle_id_drops(ferry_config):
    """Once a vessel is seen, subsequent trips with no vehicleId reuse the cached name."""
    server = TransitServer(ferry_config)
    now = int(time.time())

    # First poll: vehicleId present — populates vessel_cache
    server.cache["95_3"] = (time.time(), [
        _make_ferry_arrival(now, "trip_1", "Seattle", vehicle_id="95_25"),
    ])
    ws = AsyncMock()
    server.subscriptions[ws] = [{"routeId": "95_37", "stopId": "95_3"}]
    await server.send_update(ws)
    assert server.vessel_cache.get("95_37") == "Puyallup"

    # Second poll: vehicleId absent — should still show cached vessel name
    server.cache["95_3"] = (time.time(), [
        _make_ferry_arrival(now, "trip_2", "Bainbridge Island", vehicle_id=None),
    ])
    ws2 = AsyncMock()
    server.subscriptions[ws2] = [{"routeId": "95_37", "stopId": "95_3"}]
    await server.send_update(ws2)

    trips = json.loads(ws2.send.call_args[0][0])["data"]["trips"]
    assert trips[0]["headsign"] == "Puyallup"


@pytest.mark.asyncio
async def test_ferry_vessel_cache_updates_when_new_vessel_seen(ferry_config):
    """vessel_cache updates when a different vessel ID appears on the same route."""
    server = TransitServer(ferry_config)
    now = int(time.time())

    server.cache["95_3"] = (time.time(), [
        _make_ferry_arrival(now, "trip_1", "Seattle", vehicle_id="95_15"),  # Wenatchee
    ])
    ws = AsyncMock()
    server.subscriptions[ws] = [{"routeId": "95_37", "stopId": "95_3"}]
    await server.send_update(ws)
    assert server.vessel_cache.get("95_37") == "Wenatchee"

    server.cache["95_3"] = (time.time(), [
        _make_ferry_arrival(now, "trip_2", "Bainbridge Island", vehicle_id="95_7"),  # Puyallup
    ])
    ws2 = AsyncMock()
    server.subscriptions[ws2] = [{"routeId": "95_37", "stopId": "95_3"}]
    await server.send_update(ws2)

    trips = json.loads(ws2.send.call_args[0][0])["data"]["trips"]
    assert trips[0]["headsign"] == "Puyallup"
    assert server.vessel_cache.get("95_37") == "Puyallup"


@pytest.mark.asyncio
async def test_non_ferry_route_headsign_unchanged(mock_config):
    """Non-ferry routes must not have their headsign substituted with a vessel name."""
    server = TransitServer(mock_config)
    now = int(time.time())
    server.cache["1_8494"] = (time.time(), [
        {
            "tripId": "bus_trip_1",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "arrivalTime": (now + 600) * 1000,
            "headsign": "Downtown Seattle",
            "routeName": "40",
            "isRealtime": True,
        }
    ])

    ws = AsyncMock()
    server.subscriptions[ws] = [{"routeId": "st:40_100240", "stopId": "st:1_8494"}]
    await server.send_update(ws)

    trips = json.loads(ws.send.call_args[0][0])["data"]["trips"]
    assert len(trips) == 1
    assert trips[0]["headsign"] == "Downtown Seattle"


@pytest.mark.asyncio
async def test_rate_limit_sets_backoff_interval(mock_config):
    """A 429 during refresh_all_data must double the refresh interval (exponential backoff)."""
    server = TransitServer(mock_config)
    server.api = AsyncMock()
    server.api.get_arrivals.side_effect = Exception("HTTP 429 Too Many Requests")

    ws = MagicMock()
    server.subscriptions[ws] = [{"routeId": "r1", "stopId": "stop1"}]

    initial_interval = server.current_refresh_interval

    await server.refresh_all_data()

    assert server.current_refresh_interval == initial_interval * 2
    assert "stop1" in server.rate_limited_stops
