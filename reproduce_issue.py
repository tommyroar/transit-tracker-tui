import asyncio
import httpx
import time
from datetime import datetime, timezone
import os
import sys

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

from transit_tracker.config import TransitConfig, TransitSubscription
from transit_tracker.simulator import LEDSimulator

async def reproduce():
    # Use the live configuration from accurate_config.yaml
    config = TransitConfig.load("accurate_config.yaml")
    
    print(f"Total subscriptions: {len(config.subscriptions)}")
    for i, sub in enumerate(config.subscriptions):
        print(f"  [{i}] {sub.label}: Route={sub.route}, Stop={sub.stop}")

    sim = LEDSimulator(config, force_live=True)
    
    # We need to mock _render_led_string to capture the output instead of rendering pixels
    def mock_render(text, color="yellow"):
        print(f"RENDER: {text}")
        from rich.text import Text
        return Text(text)
    
    sim._render_led_string = mock_render

    # Manually trigger a poll
    print("Polling OBA...")
    # Group subscriptions by stop to minimize API calls
    stops_to_poll = {}
    for sub in sim.config.subscriptions:
        if sub.stop not in stops_to_poll:
            stops_to_poll[sub.stop] = []
        stops_to_poll[sub.stop].append(sub)

    base_url = "https://api.pugetsound.onebusaway.org/api/where"
    oba_key = "TEST"

    async with httpx.AsyncClient(timeout=10.0) as client:
        for stop_id, subs in stops_to_poll.items():
            stop_id_clean = stop_id.split(":")[-1] if ":" in stop_id else stop_id
            url = f"{base_url}/arrivals-and-departures-for-stop/{stop_id_clean}.json"
            response = await client.get(url, params={"key": oba_key})
            if response.status_code == 200:
                data = response.json()
                entries = data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
                new_stop_state = {"timestamp": time.time()}
                for sub in subs:
                    target_route_id = sub.route.split(":")[-1] if ":" in sub.route else sub.route
                    target_short_name = target_route_id.split("_")[-1] if "_" in target_route_id else target_route_id
                    filtered = []
                    for e in entries:
                        rid = e.get("routeId", "")
                        rsname = e.get("routeShortName", "")
                        if (rid == target_route_id or rid.split(":")[-1] == target_route_id or 
                            rsname == target_short_name or (target_short_name == "1" and rsname == "14")):
                            filtered.append(e)
                    new_stop_state[sub.route] = filtered
                    
                    # Debug print raw arrival times
                    print(f"\nSubscription {sub.label} (Route {sub.route}) at Stop {sub.stop}:")
                    for f in filtered:
                        pred = f.get("predictedArrivalTime")
                        sched = f.get("scheduledArrivalTime")
                        arr = pred or sched
                        now_ms = int(time.time() * 1000)
                        diff_min = (arr - now_ms) / 60000.0
                        print(f"  Trip {f.get('tripId')}: Predicted={pred}, Scheduled={sched}, Diff={diff_min:.1f}m")

                sim.state[stop_id] = new_stop_state

    print("\nGenerating Frame:")
    sim._generate_frame()

if __name__ == "__main__":
    asyncio.run(reproduce())
