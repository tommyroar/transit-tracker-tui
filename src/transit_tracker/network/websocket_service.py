import asyncio
import json
import websockets
from datetime import datetime
from ..config import TransitConfig

# Notifications are temporarily separated to src/transit_tracker/notifications/ntfy.py
# from ..notifications.ntfy import send_ntfy

async def run_service():
    config = TransitConfig.load()
    api_url = config.api_url
    notified_trips = set()

    async with websockets.connect(api_url) as ws:
        # Subscribe to all configured stops
        for sub in config.subscriptions:
            await ws.send(json.dumps({
                "type": "schedule:subscribe",
                "payload": {
                    "feedId": sub.feed,
                    "routeId": sub.route,
                    "stopId": sub.stop
                }
            }))

        async for message in ws:
            data = json.loads(message)
            if data.get("type") == "schedule":
                payload = data.get("payload", {})
                for trip in payload.get("trips", []):
                    trip_id = trip.get("tripId")
                    if trip_id in notified_trips:
                        continue

                    # Calculate arrival
                    arrival_str = trip.get("predictedArrivalTime") or trip.get("scheduledArrivalTime")
                    if not arrival_str:
                        continue

                    arrival_time = datetime.fromisoformat(arrival_str.replace("Z", "+00:00"))
                    now = datetime.now(arrival_time.tzinfo)
                    diff = (arrival_time - now).total_seconds() / 60

                    if 0 < diff <= config.arrival_threshold_minutes:
                        label = next((s.label for s in config.subscriptions if s.stop == payload.get("stopId")), "Transit")
                        print(f"[ALERT] {label} arriving! Bus is {int(diff)} mins away (Predicted: {arrival_time.strftime('%H:%M')})")
                        # await send_ntfy(config, f"{label} arriving!", f"Bus is {int(diff)} mins away (Predicted: {arrival_time.strftime('%H:%M')})")
                        notified_trips.add(trip_id)
