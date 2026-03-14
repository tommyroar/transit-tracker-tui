import asyncio
import websockets
import json

import pytest

@pytest.mark.asyncio
async def test_connect():
    url = "ws://127.0.0.1:8000"
    print(f"Connecting to {url}...")
    try:
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
    except (ConnectionRefusedError, OSError):
        pytest.skip("Server not running at 127.0.0.1:8000")

if __name__ == "__main__":
    asyncio.run(test_connect())
