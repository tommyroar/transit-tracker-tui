import json
import time
import pytest
import asyncio
import websockets
from transit_tracker.network.websocket_server import TransitServer
from transit_tracker.config import TransitConfig, TransitSubscription

class ProtocolValidator:
    def __init__(self, name, data):
        self.name = name
        self.data = data
        self.results = {}

    def check(self, key, condition, description):
        try:
            passed = condition(self.data)
            self.results[key] = "✅ PASS" if passed else "❌ FAIL"
        except Exception as e:
            self.results[key] = f"💥 ERR ({str(e)[:15]})"

def get_reference_trip(now):
    return {
        "tripId": "ref-123",
        "routeId": "14",
        "routeName": "14",
        "routeColor": "FF00FF",
        "stopId": "1_1234",
        "headsign": "Downtown",
        "arrivalTime": now + 600,
        "departureTime": now + 600,
        "isRealtime": True
    }

async def run_protocol_test():
    now = int(time.time())
    config = TransitConfig()
    config.subscriptions = [
        TransitSubscription(feed="st", route="14", stop="1_1234", label="14", time_offset="0min")
    ]
    
    server = TransitServer(config)
    server.cache["1_1234"] = (time.time(), [{
        "tripId": "ref-123",
        "routeId": "14",
        "stopId": "1_1234",
        "predictedArrivalTime": (now + 600) * 1000, # OBA ms
        "routeName": "14",
        "headsign": "Downtown"
    }])

    class MockWS:
        def __init__(self): self.sent = []
        async def send(self, msg): self.sent.append(json.loads(msg))
        @property
        def remote_address(self): return ("192.168.1.50", 1234)

    ws = MockWS()
    # Simulate Subscribe
    subscribe_msg = json.dumps({
        "event": "schedule:subscribe",
        "data": {"routeStopPairs": "14,1_1234"}
    })
    
    # We'll manually run the registration logic steps
    server.clients.add(ws)
    # The register loop is normally an 'async for message in ws'
    # We'll just trigger the inner logic for send_update
    server.subscriptions[ws] = [{"routeId": "14", "stopId": "1_1234"}]
    await server.send_update(ws)
    
    actual_payload = ws.sent[0]
    trip = actual_payload.get("data", {}).get("trips", [{}])[0] if "data" in actual_payload else actual_payload.get("payload", {}).get("trips", [{}])[0]

    # VALIDATORS
    local = ProtocolValidator("Local Proxy", actual_payload)
    
    # Define Reference (The "Clean" baseline we WANT to match)
    ref_payload = {
        "event": "schedule",
        "payload": {
            "trips": [get_reference_trip(now)],
            "stopId": "1_1234"
        }
    }
    reference = ProtocolValidator("Original (Reference)", ref_payload)

    # CHECK SUITE
    checks = [
        ("Event Name", lambda d: d.get("event") == "schedule", "Top-level 'event' is 'schedule'"),
        ("Payload Key", lambda d: "payload" in d, "Data is wrapped in 'payload' key"),
        ("StopId Top", lambda d: d.get("payload", {}).get("stopId") == "1_1234", "stopId present at top of payload"),
        ("Trip Count", lambda d: len(d.get("payload", {}).get("trips", [])) > 0, "At least one trip returned"),
        ("Arrival Type", lambda d: isinstance(d.get("payload", {}).get("trips", [{}])[0].get("arrivalTime"), int), "arrivalTime is an Integer"),
        ("Arrival Unit", lambda d: d.get("payload", {}).get("trips", [{}])[0].get("arrivalTime", 0) < 2*10**9, "arrivalTime is Seconds (not ms)"),
        ("Departure Exist", lambda d: "departureTime" in d.get("payload", {}).get("trips", [{}])[0], "departureTime field is present"),
        ("RouteColor Format", lambda d: d.get("payload", {}).get("trips", [{}])[0].get("routeColor") is None or "#" not in str(d.get("payload", {}).get("trips", [{}])[0].get("routeColor")), "No # in routeColor"),
        ("IsRealtime Bool", lambda d: isinstance(d.get("payload", {}).get("trips", [{}])[0].get("isRealtime"), bool), "isRealtime is Boolean"),
    ]

    for key, cond, desc in checks:
        local.check(key, cond, desc)
        reference.check(key, cond, desc)

    # PRINT TABLE
    print("\n| Protocol Feature | Local Proxy (Current) | Original (Reference) |")
    print("| :--- | :--- | :--- |")
    for key, _, _ in checks:
        print(f"| {key} | {local.results[key]} | {reference.results[key]} |")

if __name__ == "__main__":
    asyncio.run(run_protocol_test())
