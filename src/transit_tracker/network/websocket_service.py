import asyncio
import json

import websockets

from ..config import TransitConfig


async def run_service(config: TransitConfig = None):
    """
    Background service that maintains a connection to the transit API.
    Used for monitoring and potentially other background tasks.
    In 1-to-1 mode, this acts as a verification client for the local proxy.
    """
    from ..config import get_last_config_path
    
    if config is None:
        config = TransitConfig.load()
    
    current_path = get_last_config_path()
    api_url = config.api_url

    print(f"[CLIENT] Starting background monitor, connecting to {api_url}")
    
    while True:
        try:
            # Check for config reload
            new_path = get_last_config_path()
            if new_path and new_path != current_path:
                print(f"[CLIENT] Config path changed: {new_path}. Reloading...")
                config = TransitConfig.load(new_path)
                current_path = new_path
                api_url = config.api_url
                # If we're already connected, the connection will be closed and restarted below
                # or we can just continue and let the next iteration handle it.

            async with websockets.connect(api_url) as ws:
                print(f"[CLIENT] Connected to {api_url}")
                # Update current_path inside the connection context too
                # to allow breaking out if config changes while connected
                
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
                    # Check for config change while connected
                    check_path = get_last_config_path()
                    if check_path and check_path != current_path:
                        print(f"[CLIENT] Config changed while connected. Reconnecting...")
                        break

                    data = json.loads(message)
                    if data.get("event") == "schedule":
                        # Use 'data' key to match TJ Horner protocol
                        d = data.get("data") or {}
                        trips = d.get("trips", [])
                        if trips:
                            first = trips[0]
                            route = first.get("routeName", "??")
                            print(f"[CLIENT] Received update: {len(trips)} trips. Next: {route} in {first.get('arrivalTime')} (Unix)")
        except Exception as e:
            print(f"[CLIENT] Connection error: {e}. Retrying in 10s...")
            await asyncio.sleep(10)
