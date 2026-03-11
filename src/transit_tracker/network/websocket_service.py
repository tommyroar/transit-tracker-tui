import asyncio
import json
import websockets
from datetime import datetime, timezone
from ..config import TransitConfig

# Notifications are temporarily separated to src/transit_tracker/notifications/ntfy.py
# from .notifications.ntfy import send_ntfy

async def run_service(config: TransitConfig = None):
    if config is None:
        config = TransitConfig.load()
    api_url = config.api_url
    notified_trips = set()

    print(f"[CLIENT] Starting notification client, connecting to {api_url}")
    
    while True:
        try:
            async with websockets.connect(api_url) as ws:
                print(f"[CLIENT] Connected to {api_url}")
                # Subscribe to all configured stops
                for sub in config.subscriptions:
                    # Support both formats for subscription
                    await ws.send(json.dumps({
                        "type": "schedule:subscribe",
                        "payload": {
                            "feedId": sub.feed,
                            "routeId": sub.route,
                            "stopId": sub.stop
                        }
                    }))
                    # Also send in TJ Horner format just in case
                    r_id = f"{sub.feed}:{sub.route}" if ":" not in sub.route else sub.route
                    s_id = f"{sub.feed}:{sub.stop}" if ":" not in sub.stop else sub.stop
                    await ws.send(json.dumps({
                        "event": "schedule:subscribe",
                        "data": {
                            "routeStopPairs": f"{r_id},{s_id}"
                        }
                    }))

                async for message in ws:
                    data = json.loads(message)
                    # Support both 'type' and 'event'
                    if data.get("type") == "schedule" or data.get("event") == "schedule":
                        payload = data.get("payload") or data.get("data") or {}
                        for trip in payload.get("trips", []):
                            trip_id = trip.get("tripId")
                            if trip_id in notified_trips:
                                continue

                            # Calculate arrival
                            arrival_str = trip.get("arrivalTime") or trip.get("predictedArrivalTime") or trip.get("scheduledArrivalTime")
                            if not arrival_str:
                                continue

                            if isinstance(arrival_str, str):
                                arrival_time = datetime.fromisoformat(arrival_str.replace("Z", "+00:00"))
                            else:
                                # Assume unix timestamp (ms or sec)
                                ts = arrival_str / 1000 if arrival_str > 10**12 else arrival_str
                                arrival_time = datetime.fromtimestamp(ts, tz=timezone.utc)

                            now = datetime.now(arrival_time.tzinfo)
                            diff = (arrival_time - now).total_seconds() / 60

                            if 0 < diff <= config.arrival_threshold_minutes:
                                route_name = trip.get("routeName") or trip.get("routeShortName") or "Bus"
                                headsign = trip.get("headsign") or "Transit"
                                label = f"{route_name} to {headsign}"
                                print(f"[ALERT] {label} arriving! {int(diff)} mins away (Predicted: {arrival_time.strftime('%H:%M')})")
                                # await send_ntfy(config, f"{label} arriving!", f"Bus is {int(diff)} mins away (Predicted: {arrival_time.strftime('%H:%M')})")
                                notified_trips.add(trip_id)
        except Exception as e:
            print(f"[CLIENT] Connection error: {e}. Retrying in 10s...")
            await asyncio.sleep(10)
