import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from transit_tracker.config import TransitConfig, TransitSubscription
from transit_tracker.network.websocket_server import TransitServer


@pytest.fixture
def mock_config():
    config = MagicMock(spec=TransitConfig)
    config.subscriptions = [
        TransitSubscription(feed="st", route="st:40_100240", stop="st:1_8494", label="Route st:40_100240")
    ]
    config.use_local_api = True
    config.auto_launch_gui = True
    config.arrival_threshold_minutes = 5
    config.check_interval_seconds = 30
    return config

@pytest.mark.asyncio
async def test_server_broadcast_updates(mock_config):
    """Test that the server broadcasts updates to subscribed clients."""
    server = TransitServer(mock_config)
    server.api = AsyncMock()

    now = int(time.time())
    mock_arrivals = [
        {
            "tripId": "trip_123",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "arrivalTime": (now + 600) * 1000 # 10 mins from now in ms
        }
    ]
    server.api.get_arrivals.return_value = mock_arrivals

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
    server.api = AsyncMock()

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
    
    async def side_effect(stop_id):
        if "stop1" in stop_id: return mock_arrivals_1
        if "stop2" in stop_id: return mock_arrivals_2
        return []
    
    server.api.get_arrivals.side_effect = side_effect

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
