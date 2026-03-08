import asyncio
import json
import websockets
import sys

async def test_ws():
    api_url = "wss://tt.horner.tj"
    print(f"Connecting to {api_url}...")
    try:
        async with websockets.connect(api_url) as ws:
            print("Connected!")
            
            pairs = "st:40_100240,st:1_8494;st:1_100039,st:1_11920"
            payload = {
                "event": "schedule:subscribe",
                "data": {
                    "routeStopPairs": pairs,
                    "limit": 3
                }
            }
            await ws.send(json.dumps(payload))

            print("Waiting for message...")
            message = await asyncio.wait_for(ws.recv(), timeout=20)
            data = json.loads(message)
            if data.get("event") == "schedule":
                trips = data.get("data", {}).get("trips", [])
                if trips:
                    print(f"Keys in trip: {list(trips[0].keys())}")
                    print(f"Sample trip: {trips[0]}")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    asyncio.run(test_ws())
