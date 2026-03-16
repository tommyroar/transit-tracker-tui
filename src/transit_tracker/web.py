import asyncio
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List

from .config import TransitConfig
from .transit_api import TransitAPI


async def resolve_stop_coordinates(config: TransitConfig) -> List[Dict[str, Any]]:
    """Fetch lat/lon for all configured stops from the OBA API."""
    api = TransitAPI()
    try:
        tasks = [api.get_stop(stop.stop_id) for stop in config.transit_tracker.stops]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        stops = []
        for stop_cfg, result in zip(config.transit_tracker.stops, results, strict=True):
            if isinstance(result, Exception):
                print(
                    f"[WEB] Warning: could not fetch stop {stop_cfg.stop_id}: {result}"
                )
                continue
            if result is None:
                print(f"[WEB] Warning: stop {stop_cfg.stop_id} not found")
                continue
            stops.append(
                {
                    "stop_id": stop_cfg.stop_id,
                    "name": result["name"],
                    "lat": result["lat"],
                    "lon": result["lon"],
                    "label": stop_cfg.label or result["name"],
                }
            )
        return stops
    finally:
        await api.close()


def generate_api_spec(config: TransitConfig) -> str:
    """Generate a JSON API specification with example payloads from the live config."""
    # Build example routeStopPairs string from config
    pairs_parts = []
    for sub in config.subscriptions:
        offset_sec = 0
        match = re.search(r"(-?\d+)", sub.time_offset)
        if match:
            offset_sec = int(match.group(1)) * 60
        pairs_parts.append(f"{sub.route},{sub.stop},{offset_sec}")
    pairs_str = ";".join(pairs_parts)

    # Build example trip objects from config subscriptions
    example_bus_trips = []
    example_ferry_trips = []
    now_ts = 1773534000  # static example timestamp

    for i, sub in enumerate(config.subscriptions):
        is_ferry = sub.route.startswith("wsf:") or sub.route.startswith("95_")
        trip = {
            "tripId": f"{'95' if is_ferry else '40'}_{141500000 + i}",
            "routeId": sub.route.replace("st:", "").replace("wsf:", "95_"),
            "routeName": sub.label.split(" - ")[0] if " - " in sub.label else sub.label,
            "routeColor": None,
            "stopId": sub.stop,
            "headsign": "Puyallup" if is_ferry else "Downtown Seattle",
            "arrivalTime": now_ts + (i * 600),
            "departureTime": now_ts + (i * 600) + 60,
            "isRealtime": True,
        }
        if is_ferry:
            example_ferry_trips.append(trip)
        else:
            example_bus_trips.append(trip)

    spec = {
        "info": {
            "title": "Transit Tracker WebSocket API",
            "description": (
                "JSON-over-WebSocket protocol for real-time transit arrivals. "
                "Compatible with the TJ Horner transit-tracker-api and ESP32 LED matrix firmware."
            ),
            "version": "1.0.0",
            "websocket_url": config.api_url or "ws://localhost:8000",
            "web_url": "http://localhost:8080",
        },
        "config": {
            "check_interval_seconds": config.check_interval_seconds,
            "arrival_threshold_minutes": config.arrival_threshold_minutes,
            "time_display": config.time_display,
            "num_panels": config.num_panels,
            "panel_size": f"{config.panel_width}x{config.panel_height}",
            "scroll_headsigns": config.scroll_headsigns,
            "subscriptions": [
                {
                    "feed": sub.feed,
                    "route": sub.route,
                    "stop": sub.stop,
                    "label": sub.label,
                    "time_offset": sub.time_offset,
                }
                for sub in config.subscriptions
            ],
        },
        "messages": {
            "client_to_server": {
                "schedule:subscribe": {
                    "description": "Subscribe to arrival updates for route/stop pairs.",
                    "fields": {
                        "event": {"type": "string", "value": "schedule:subscribe"},
                        "client_name": {
                            "type": "string",
                            "optional": True,
                            "description": "Friendly name for server dashboard",
                        },
                        "limit": {
                            "type": "int",
                            "optional": True,
                            "default": 3,
                            "description": "Max trips per push",
                        },
                        "data.routeStopPairs": {
                            "type": "string",
                            "description": (
                                "Semicolon-separated entries: routeId,stopId[,offsetSec]. "
                                "If empty, server uses its own config."
                            ),
                        },
                    },
                    "example": {
                        "event": "schedule:subscribe",
                        "client_name": "LED Matrix",
                        "limit": 3,
                        "data": {"routeStopPairs": pairs_str},
                    },
                },
            },
            "server_to_client": {
                "schedule": {
                    "description": (
                        "Pushed on every data refresh cycle "
                        f"(every {config.check_interval_seconds}s)."
                    ),
                    "fields": {
                        "event": {"type": "string", "value": "schedule"},
                        "data.trips": {
                            "type": "array<Trip>",
                            "description": "Sorted by arrivalTime ascending, capped by client limit",
                        },
                    },
                    "examples": {
                        "bus": {
                            "event": "schedule",
                            "data": {
                                "trips": example_bus_trips[:2]
                                if example_bus_trips
                                else [
                                    {
                                        "tripId": "40_141953498",
                                        "routeId": "40_100240",
                                        "routeName": "554",
                                        "routeColor": "BF34A4",
                                        "stopId": "st:1_8494",
                                        "headsign": "Downtown Seattle",
                                        "arrivalTime": 1773534120,
                                        "departureTime": 1773534180,
                                        "isRealtime": True,
                                    }
                                ],
                            },
                        },
                        "ferry": {
                            "event": "schedule",
                            "data": {
                                "trips": example_ferry_trips[:2]
                                if example_ferry_trips
                                else [
                                    {
                                        "tripId": "95_73503142611",
                                        "routeId": "95_73",
                                        "routeName": "Seattle - Bainbridge Island",
                                        "routeColor": None,
                                        "stopId": "wsf:7",
                                        "headsign": "Puyallup",
                                        "arrivalTime": 1773534900,
                                        "departureTime": 1773534900,
                                        "isRealtime": True,
                                    }
                                ],
                            },
                        },
                    },
                },
                "heartbeat": {
                    "description": "Sent every 10 seconds to keep the connection alive.",
                    "example": {"event": "heartbeat", "data": None},
                },
            },
        },
        "types": {
            "Trip": {
                "tripId": {"type": "string", "description": "OBA trip identifier"},
                "routeId": {
                    "type": "string",
                    "description": "OBA route ID (e.g., 40_100240, 95_73)",
                },
                "routeName": {
                    "type": "string",
                    "description": "Short route name (e.g., '554', 'SEA-BI')",
                },
                "routeColor": {
                    "type": "string|null",
                    "description": "Hex color without # (e.g., 'BF34A4'), or null",
                },
                "stopId": {
                    "type": "string",
                    "description": "Stop ID as subscribed (preserves st:/wsf: prefix)",
                },
                "headsign": {
                    "type": "string",
                    "description": (
                        "Destination label. For live-tracked ferries, "
                        "replaced with vessel name (e.g., 'Puyallup')"
                    ),
                },
                "arrivalTime": {
                    "type": "int",
                    "description": (
                        "Unix timestamp (seconds) — adjusted for time_offset "
                        "and arrival/departure mode. "
                        "ESP32 computes: display_mins = (arrivalTime - now()) / 60"
                    ),
                },
                "departureTime": {
                    "type": "int",
                    "description": "Unix timestamp (seconds) — departure time with offset",
                },
                "isRealtime": {
                    "type": "bool",
                    "description": "true if GPS-predicted, false if scheduled",
                },
            },
        },
        "id_prefixes": {
            "st:": "Sound Transit / King County Metro (stripped to OBA format, e.g., st:1_8494 → 1_8494)",
            "wsf:": "Washington State Ferries (mapped to agency 95, e.g., wsf:7 → 95_7)",
        },
        "ferry": {
            "vessel_mapping": "When vehicleId is present, headsign is replaced with vessel name from WSF_VESSELS dict",
            "arrival_vs_departure": (
                "Determined per-trip by OBA arrivalEnabled/departureEnabled flags. "
                "Origin docks show departure time; destination docks show arrival time."
            ),
            "abbreviations": [
                {"original": a.original, "short": a.short}
                for a in config.transit_tracker.abbreviations
            ],
        },
        "rate_limiting": {
            "backoff": "On HTTP 429, refresh interval doubles (max 600s). Recovers 20% per successful cycle.",
            "per_stop_cooldown": "Rate-limited stops have individual cooldown timestamps.",
        },
    }

    return json.dumps(spec, indent=2)


def generate_index_html(pages: List[Dict[str, str]]) -> str:
    """Generate an index page listing available web pages."""
    links = "".join(
        f'<li><a href="{p["path"]}">{p["name"]}</a> — {p["description"]}</li>'
        for p in pages
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Transit Tracker</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 600px; margin: 60px auto; padding: 0 20px; color: #1a202c; }}
  h1 {{ margin-bottom: 8px; }}
  p {{ color: #666; margin-bottom: 24px; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ margin-bottom: 12px; }}
  a {{ color: #f58220; text-decoration: none; font-weight: 600; font-size: 18px; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
  <h1>Transit Tracker</h1>
  <p>Available pages:</p>
  <ul>{links}</ul>
</body>
</html>"""


class TransitWebHandler(BaseHTTPRequestHandler):
    """HTTP request handler with a routes dict for extensibility."""

    routes: Dict[str, str] = {}

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        content = self.routes.get(path)
        if content is not None:
            content_type = (
                "application/json" if path.startswith("/api/") else "text/html"
            )
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>404 Not Found</h1>")

    def log_message(self, format, *args):
        print(f"[WEB] {args[0]}")


async def run_web(config: TransitConfig, host: str = "0.0.0.0", port: int = None):
    """Start the Transit Tracker web server with API spec and stop data."""
    if port is None:
        port = int(os.environ.get("PORT", 8080))

    print("[WEB] Resolving stop coordinates...")
    stops = await resolve_stop_coordinates(config)
    if not stops:
        print("[WEB] No stops found in config. Add stops first with 'transit-tracker'.")
        return

    print(f"[WEB] Resolved {len(stops)} stops")

    stops_json = json.dumps(stops, indent=2)
    spec_json = generate_api_spec(config)

    pages = [
        {
            "path": "/api/spec",
            "name": "API Spec",
            "description": "Full WebSocket API specification with example payloads",
        },
        {
            "path": "/api/stops",
            "name": "Stops",
            "description": "Configured stop coordinates as JSON",
        },
    ]
    index_html = generate_index_html(pages)

    TransitWebHandler.routes = {
        "/": index_html,
        "/api/spec": spec_json,
        "/api/stops": stops_json,
    }

    server = HTTPServer((host, port), TransitWebHandler)
    print(f"[WEB] Transit Tracker web server at http://{host}:{port}")
    print("[WEB]   /api/spec   — WebSocket API specification")
    print("[WEB]   /api/stops  — Stop coordinates JSON")
    print("[WEB] Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[WEB] Shutting down...")
        server.shutdown()
