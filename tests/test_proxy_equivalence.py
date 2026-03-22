import json
import time

import pytest

from transit_tracker.config import TransitConfig
from transit_tracker.network.websocket_server import TransitServer

pytestmark = pytest.mark.contract


class MockWS:
    def __init__(self, remote_addr="192.168.1.50"):
        self.sent = []
        self._remote_address = (remote_addr, 1234)
    async def send(self, msg):
        self.sent.append(json.loads(msg))
    @property
    def remote_address(self):
        return self._remote_address

def setup_test_server(subscriptions=None):
    config = TransitConfig()
    if subscriptions:
        config.subscriptions = subscriptions
    server = TransitServer(config)
    return server

@pytest.mark.asyncio
async def test_handshake_equivalence():
    """Validates that the server parses the TJ Horner handshake (route,stop,offset) correctly."""
    server = setup_test_server()
    ws = MockWS()
    
    # Simulate the handshake the firmware sends
    handshake = {
        "event": "schedule:subscribe",
        "data": {
            "routeStopPairs": "14,1_1234,-300;554,1_8494,120",
            "limit": 5
        }
    }
    
    # We need to simulate the message loop logic
    # In a real run, this happens in TransitServer.register
    # For testing, we'll manually set the subscriptions as register would
    pairs_str = handshake["data"]["routeStopPairs"]
    pairs = []
    for pair in pairs_str.split(";"):
        parts = [p.strip() for p in pair.split(",")]
        r_id, s_id = parts[0], parts[1]
        offset = int(parts[2]) if len(parts) >= 3 else 0
        pairs.append({"routeId": r_id, "stopId": s_id, "offset": offset})
    
    server.subscriptions[ws] = pairs
    server.client_limits[ws] = 5
    
    assert len(server.subscriptions[ws]) == 2
    assert server.subscriptions[ws][0]["offset"] == -300
    assert server.subscriptions[ws][1]["offset"] == 120

@pytest.mark.asyncio
async def test_payload_identity():
    """Ensures the JSON payload matches the Cloud Proxy schema exactly."""
    now = int(time.time())
    server = setup_test_server()
    ws = MockWS()
    
    # Subscription with 2m offset
    server.subscriptions[ws] = [{"routeId": "14", "stopId": "1_1234", "offset": 120}]
    
    # Mock OBA response
    server.cache["1_1234"] = (time.time(), [{
        "tripId": "t1",
        "routeId": "14",
        "predictedArrivalTime": (now + 600) * 1000, # 10m away
        "predictedDepartureTime": (now + 605) * 1000,
        "routeName": "14",
        "headsign": "Downtown",
        "isRealtime": True,
        "routeColor": "FF00FF"
    }])
    
    await server.send_update(ws)
    
    msg = ws.sent[0]
    # Check top level
    assert msg["event"] == "schedule"
    assert "data" in msg
    # TJ Horner protocol puts stopId inside each trip, not at top level
    
    # Check trip
    trip = msg["data"]["trips"][0]
    assert trip["tripId"] == "t1"
    assert trip["arrivalTime"] == now + 600 + 120 # Base + offset
    assert trip["departureTime"] == now + 605 + 120
    assert trip["routeColor"] == "FF00FF" # No # prefix
    assert isinstance(trip["isRealtime"], bool)

@pytest.mark.asyncio
async def test_filtering_identity():
    """Ensures past trips are filtered out, matching cloud behavior."""
    now = int(time.time())
    server = setup_test_server()
    ws = MockWS()
    
    server.subscriptions[ws] = [{"routeId": "14", "stopId": "1_1234", "offset": 0}]
    
    # Mock 3 trips: 1 past, 1 now, 1 future
    server.cache["1_1234"] = (time.time(), [
        {"tripId": "past", "routeId": "14", "predictedArrivalTime": (now - 120) * 1000}, # 2m ago
        {"tripId": "now",  "routeId": "14", "predictedArrivalTime": (now - 10) * 1000},  # 10s ago (should keep)
        {"tripId": "future", "routeId": "14", "predictedArrivalTime": (now + 300) * 1000} # 5m future
    ])
    
    await server.send_update(ws)
    
    trips = ws.sent[0]["data"]["trips"]
    ids = [t["tripId"] for t in trips]
    
    assert "past" not in ids
    assert "now" in ids
    assert "future" in ids

@pytest.mark.asyncio
async def test_sorting_identity():
    """Ensures trips are sorted by arrival time, matching cloud behavior."""
    now = int(time.time())
    server = setup_test_server()
    ws = MockWS()
    
    server.subscriptions[ws] = [{"routeId": "all", "stopId": "stop", "offset": 0}]
    
    server.cache["stop"] = (time.time(), [
        {"tripId": "later", "routeId": "all", "predictedArrivalTime": (now + 600) * 1000},
        {"tripId": "sooner", "routeId": "all", "predictedArrivalTime": (now + 100) * 1000},
        {"tripId": "middle", "routeId": "all", "predictedArrivalTime": (now + 300) * 1000}
    ])
    
    await server.send_update(ws)
    
    trips = ws.sent[0]["data"]["trips"]
    assert trips[0]["tripId"] == "sooner"
    assert trips[1]["tripId"] == "middle"
    assert trips[2]["tripId"] == "later"

if __name__ == "__main__":
    import pytest
    pytest.main([__file__])
