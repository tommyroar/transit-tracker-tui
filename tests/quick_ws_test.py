import asyncio
import websockets
import json

async def test_connect():
    url = "ws://127.0.0.1:8000"
    print(f"Connecting to {url}...")
    async with websockets.connect(url) as ws:
        print("Connected!")
        sub = {
            "event": "schedule:subscribe",
            "data": {
                "routeStopPairs": "14,1_11920,0",
                "limit": 3
            }
        }
        await ws.send(json.dumps(sub))
        print("Sent subscribe")
        msg = await ws.recv()
        print(f"Received: {msg[:100]}...")

if __name__ == "__main__":
    asyncio.run(test_connect())
