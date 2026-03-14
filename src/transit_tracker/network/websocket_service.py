import asyncio
import json
import websockets
from datetime import datetime, timezone
from ..config import TransitConfig

async def run_service(config: TransitConfig = None):
    """
    Background service that maintains a connection to the transit API.
    Used for monitoring and potentially other background tasks.
    In 1-to-1 mode, this acts as a verification client for the local proxy.
    """
    if config is None:
        config = TransitConfig.load()
    api_url = config.api_url

    print(f"[CLIENT] Starting background monitor, connecting to {api_url}")
    
    while True:
        try:
            async with websockets.connect(api_url) as ws:
                print(f"[CLIENT] Connected to {api_url}")
                # Build TJ Horner style routeStopPairs string for all subscriptions
                pairs = []
                for sub in config.subscriptions:
                    r_id = f"{sub.feed}:{sub.route}" if ":" not in sub.route else sub.route
                    s_id = f"{sub.feed}:{sub.stop}" if ":" not in sub.stop else sub.stop
                    pairs.append(f"{r_id},{s_id}")
                
                if pairs:
                    await ws.send(json.dumps({
                        "event": "schedule:subscribe",
                        "client_name": "BackgroundMonitor",
                        "data": {
                            "routeStopPairs": ";".join(pairs)
                        }
                    }))

                async for message in ws:
                    # In 1-to-1 mode, we just keep the connection alive
                    # and potentially log updates for debugging.
                    data = json.loads(message)
                    if data.get("event") == "schedule":
                        payload = data.get("payload") or {}
                        trips = payload.get("trips", [])
                        if trips:
                            first = trips[0]
                            route = first.get("routeName", "??")
                            print(f"[CLIENT] Received update: {len(trips)} trips. Next: {route} in {first.get('arrivalTime')} (Unix)")
        except Exception as e:
            print(f"[CLIENT] Connection error: {e}. Retrying in 10s...")
            await asyncio.sleep(10)
