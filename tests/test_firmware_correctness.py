import json
import time
from datetime import datetime, timezone

from transit_tracker.config import TransitConfig, TransitSubscription
from transit_tracker.network.websocket_server import TransitServer
from transit_tracker.simulator import LEDSimulator


def test_offset_contract_consistency():
    """
    Verifies that the Server's 'spoofing' and the Simulator's 'offsetting' 
    result in the SAME final display value for both Local and Remote APIs.
    """
    now_ts = int(time.time())
    arrival_ts = now_ts + 600 # 10 minutes from now
    
    config = TransitConfig()
    from transit_tracker.config import TransitStop
    config.transit_tracker.stops = [
        TransitStop(stop_id="1_1234", routes=["14"], time_offset="-2min")
    ]
    config.sync_internal_state()
    # Ensure routeId in mock trip matches what normalize_id expects
    arrival_ts = now_ts + 600
    mock_oba_trip = {
        "tripId": "trip1",
        "routeId": "st:14", # Match the synced st: prefix if needed
        "stopId": "1_1234",
        "predictedArrivalTime": arrival_ts * 1000,
        "routeName": "14",
        "headsign": "Downtown"
    }
    
    # 1. TEST LOCAL API PATH (Server spoofs, Simulator stays dumb)
    server = TransitServer(config)
    
    # Simulate server processing
    # We need to mock the websocket to capture the send
    class MockWS:
        def __init__(self): self.sent = None
        async def send(self, msg): self.sent = json.loads(msg)
        @property
        def remote_address(self): return ("127.0.0.1", 1234)

    ws = MockWS()
    server.subscriptions[ws] = [{"routeId": "st:14", "stopId": "1_1234", "offset": -120}]    
    # We'll manually trigger the internal logic of send_update or similar
    # But get_arrivals_cached is async and hits API. Let's mock the cache.
    server.cache["1_1234"] = (time.time(), [mock_oba_trip])
    
    import asyncio
    asyncio.run(server.send_update(ws))
    
    sent_data = ws.sent["payload"]["trips"][0]
    # The server should have subtracted 2 mins (120s) from the arrival time
    expected_spoofed = arrival_ts - 120
    assert sent_data["arrivalTime"] == expected_spoofed
    
    # Now run this through the Simulator WITH use_local_api=True
    config.use_local_api = True
    config.api_url = "ws://localhost:8000"
    sim = LEDSimulator(config)
    sim.state["live"] = {"trips": ws.sent["payload"]["trips"], "timestamp": time.time()}
    
    deps = sim.get_upcoming_departures(reference_time=datetime.fromtimestamp(now_ts, tz=timezone.utc))
    assert deps[0]["diff"] == 8 # 10m - 2m offset = 8m
    
    # 2. TEST REMOTE API PATH (Simulator stays 'dumb' now, matching HW)
    # The simulator (like the hardware) now expects the proxy to have handled
    # any necessary time offsets. If we point it at a raw API (like tt.horner.tj)
    # it should just show the raw arrival time.
    config.use_local_api = False
    config.api_url = "wss://tt.horner.tj/"
    
    # Raw data from remote (no spoofing)
    remote_data = [{
        "tripId": "trip1",
        "routeId": "st:14", 
        "stopId": "1_1234",
        "arrivalTime": arrival_ts, # 10m from now
        "routeName": "14",
        "headsign": "Downtown",
        "isRealtime": True
    }]
    sim.state["live"] = {"trips": remote_data, "timestamp": time.time()}
    
    deps_remote = sim.get_upcoming_departures(reference_time=datetime.fromtimestamp(now_ts, tz=timezone.utc))
    assert deps_remote[0]["diff"] == 10 # Shows raw 10m arrival

def test_now_bug_reproduction():
    """
    Ensures that a missing departureTime doesn't result in a massive negative 
    number (the 'Now' bug) when processed by the simulator logic.
    """
    now_ts = int(time.time())
    # Trip arriving in 5 mins
    arrival_ts = now_ts + 300
    
    # Payload with ONLY arrivalTime (simulating the bug-prone state)
    trip_data = {
        "tripId": "trip1",
        "routeId": "14",
        "stopId": "1_1234",
        "arrivalTime": arrival_ts,
        "isRealtime": True
    }
    
    config = TransitConfig()
    config.subscriptions = [TransitSubscription(feed="st", route="14", stop="1_1234", label="14")]
    sim = LEDSimulator(config)
    sim.state["live"] = {"trips": [trip_data], "timestamp": time.time()}
    
    deps = sim.get_upcoming_departures(reference_time=datetime.fromtimestamp(now_ts, tz=timezone.utc))
    assert deps[0]["diff"] == 5
    
def test_dumb_firmware_compatibility():
    """
    Verifies that the server produces a JSON payload that a 'dumb' firmware
    (which only does arrivalTime - now) would render correctly with offsets.
    """
    now_ts = int(time.time())
    # Real arrival in 15 mins
    arrival_ts = now_ts + 900
    # Offset of -5 mins (should show as 10 mins)
    
    config = TransitConfig()
    sub = TransitSubscription(feed="st", route="40", stop="1_8494", label="40", time_offset="-5min")
    config.subscriptions = [sub]
    
    server = TransitServer(config)
    server.cache["1_8494"] = (time.time(), [{
        "tripId": "trip-40",
        "routeId": "40",
        "stopId": "1_8494",
        "predictedArrivalTime": arrival_ts * 1000,
        "routeName": "40",
        "headsign": "Northgate"
    }])
    
    class MockWS:
        def __init__(self): self.sent = None
        async def send(self, msg): self.sent = json.loads(msg)
        @property
        def remote_address(self): return ("192.168.1.50", 1234) # Hardware IP

    ws = MockWS()
    server.subscriptions[ws] = [{"routeId": "40", "stopId": "1_8494", "offset": -300}]
    
    import asyncio
    asyncio.run(server.send_update(ws))
    
    trip_json = ws.sent["payload"]["trips"][0]
    
    # --- THE DUMB FIRMWARE MODEL ---
    # This represents exactly what the C++ code on the ESP32 does:
    # int display_mins = (arrival_time_from_json - internal_sntp_now) / 60;
    internal_sntp_now = now_ts
    display_mins = (trip_json["arrivalTime"] - internal_sntp_now) // 60
    
    # It should be 10 minutes (15m real - 5m offset)
    assert display_mins == 10
    print(f"\nDumb Firmware Calculation: ({trip_json['arrivalTime']} - {internal_sntp_now}) / 60 = {display_mins}m")
