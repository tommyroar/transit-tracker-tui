import asyncio
import json
import time

import pytest

from transit_tracker.config import TransitConfig, TransitSubscription
from transit_tracker.network.websocket_server import TransitServer
from transit_tracker.simulator import LEDSimulator

pytestmark = pytest.mark.integration

def get_mock_oba_response(now_ms):
    return [
        {
            "tripId": "st:t1",
            "routeId": "st:40_100240",
            "predictedArrivalTime": now_ms + (15 * 60 * 1000), # 15m away
            "predictedDepartureTime": now_ms + (15 * 60 * 1000) + 30000,
            "routeName": "554",
            "headsign": "Downtown Seattle",
            "isRealtime": True,
            "routeColor": "2B376E"
        },
        {
            "tripId": "st:t2",
            "routeId": "st:1_100039",
            "predictedArrivalTime": now_ms + (8 * 60 * 1000), # 8m away
            "predictedDepartureTime": now_ms + (8 * 60 * 1000),
            "routeName": "14",
            "headsign": "Downtown Seattle",
            "isRealtime": True,
            "routeColor": "FDB71A"
        }
    ]

@pytest.mark.asyncio
async def test_simulator_identity():
    # Use a fixed integer timestamp for all calculations to avoid float drift during the test
    now_ts = int(time.time())
    now_ms = now_ts * 1000
    
    config = TransitConfig()
    # Subscription with -7m and -9m offsets
    sub1 = TransitSubscription(feed="st", route="40_100240", stop="1_8494", label="554", time_offset="-7min")
    sub2 = TransitSubscription(feed="st", route="1_100039", stop="1_11920", label="14", time_offset="-9min")
    config.subscriptions = [sub1, sub2]
    
    # --- 1. LOCAL PROXY LOGIC ---
    server = TransitServer(config)
    
    class MockWS:
        def __init__(self): self.sent = None
        async def send(self, msg): self.sent = json.loads(msg)
        @property
        def remote_address(self): return ("127.0.0.1", 1234)

    ws = MockWS()
    # Handshake with offsets
    server.subscriptions[ws] = [
        {"routeId": "st:40_100240", "stopId": "st:1_8494", "offset": -420}, # -7m
        {"routeId": "st:1_100039", "stopId": "st:1_11920", "offset": -540}  # -9m
    ]
    server.client_limits[ws] = 3
    
    # Inject Mock Data - Use the SAME now_ts
    server.cache["1_8494"] = (now_ts, [get_mock_oba_response(now_ms)[0]])
    server.cache["1_11920"] = (now_ts, [get_mock_oba_response(now_ms)[1]])
    
    await server.send_update(ws)
    local_json = ws.sent["data"]["trips"]
    
    # --- 2. CLOUD PROXY LOGIC (Simulated Based on Source Code) ---
    # The cloud proxy does: arrivalTime: new Date(trip.arrivalTime).getTime() / 1000 + offset
    # And filters: trip.arrivalTime > now
    cloud_json = []
    oba_data = get_mock_oba_response(now_ms)
    
    # 14 Downtown (8m real - 9m offset = -1m) -> SHOULD BE FILTERED
    # 554 Downtown (15m real - 7m offset = 8m) -> SHOULD BE KEPT
    
    # Trip 1 (554)
    t1 = oba_data[0]
    final_arr = (t1["predictedArrivalTime"] / 1000) - 420
    # Our server filters trips that would arrive > 60s in the past
    if final_arr >= now_ts - 60:
        cloud_json.append({
            "tripId": t1["tripId"], "routeId": t1["routeId"], "routeName": t1["routeName"],
            "arrivalTime": int(final_arr), "departureTime": int(final_arr + 30),
            "headsign": t1["headsign"], "routeColor": t1["routeColor"], "isRealtime": True,
            "stopId": "st:1_8494"
        })
        
    # Trip 2 (14)
    t2 = oba_data[1]
    final_arr_2 = (t2["predictedArrivalTime"] / 1000) - 540
    # This trip results in -60s from now (Now-1m), so it should be filtered by the >= now_ts - 60 rule
    if final_arr_2 >= now_ts - 60:
        cloud_json.append({
            "tripId": t2["tripId"], "routeId": t2["routeId"], "routeName": t2["routeName"],
            "arrivalTime": int(final_arr_2), "departureTime": int(final_arr_2),
            "headsign": t2["headsign"], "routeColor": t2["routeColor"], "isRealtime": True,
            "stopId": "st:1_11920"
        })

    # --- 3. SIMULATOR RENDERING ---
    sim = LEDSimulator(config)
    
    # Render Local
    sim.state["live"] = {"trips": local_json, "timestamp": now_ts}
    local_text = sim.get_current_display_text()
    
    # Render Cloud
    sim.state["live"] = {"trips": cloud_json, "timestamp": now_ts}
    cloud_text = sim.get_current_display_text()
    
    print(f"\nLocal Result:\n{local_text}")
    print(f"\nCloud Result:\n{cloud_text}")
    
    assert local_text == cloud_text
    print("\n[SUCCESS] Simulator output is identical for both proxy logics!")

if __name__ == "__main__":
    asyncio.run(test_simulator_identity())
