import asyncio
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List

from .config import TransitConfig
from .transit_api import TransitAPI


import csv
import sqlite3
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_GTFS_DIR = _PROJECT_ROOT / "data" / "gtfs"
_GTFS_DB = _PROJECT_ROOT / "data" / "gtfs_index.sqlite"


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
            "direction_filtering": (
                "Ferry trips are filtered by direction at the terminal. "
                "At a departure terminal (display_mode='departure'), inbound arrivals "
                "(arrivalEnabled=True, departureEnabled=False) are skipped, and vice versa. "
                "Unlike buses, ferries with an expired preferred time are dropped entirely "
                "rather than falling back to the alternate time."
            ),
            "realtime_detection": (
                "Ferry isRealtime is based on vehicleId presence in OBA data, "
                "not the predicted flag used for buses. A ferry trip is realtime "
                "only when a vessel is actively tracked."
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


def generate_spec_html(spec_json: str) -> str:
    """Generate a styled HTML documentation page from the API spec JSON."""
    spec = json.loads(spec_json)
    info = spec["info"]
    config = spec["config"]
    messages = spec["messages"]
    types = spec["types"]
    ferry = spec.get("ferry", {})
    rate = spec.get("rate_limiting", {})
    prefixes = spec.get("id_prefixes", {})

    def json_block(obj: Any) -> str:
        return json.dumps(obj, indent=2)

    # Build subscription rows
    sub_rows = ""
    for s in config["subscriptions"]:
        sub_rows += (
            f"<tr><td><code>{s['feed']}</code></td>"
            f"<td><code>{s['route']}</code></td>"
            f"<td><code>{s['stop']}</code></td>"
            f"<td>{s['label']}</td>"
            f"<td>{s['time_offset']}</td></tr>\n"
        )

    # Build Trip type rows
    trip_rows = ""
    for field, meta in types.get("Trip", {}).items():
        trip_rows += (
            f"<tr><td><code>{field}</code></td>"
            f"<td><code>{meta['type']}</code></td>"
            f"<td>{meta['description']}</td></tr>\n"
        )

    # Build prefix rows
    prefix_rows = ""
    for prefix, desc in prefixes.items():
        prefix_rows += f"<tr><td><code>{prefix}</code></td><td>{desc}</td></tr>\n"

    # Build abbreviation rows
    abbr_rows = ""
    for a in ferry.get("abbreviations", []):
        abbr_rows += (
            f"<tr><td>{a['original']}</td><td><code>{a['short']}</code></td></tr>\n"
        )

    # Client message
    sub_msg = messages["client_to_server"]["schedule:subscribe"]
    sub_fields_rows = ""
    for fname, fmeta in sub_msg["fields"].items():
        opt = " <em>(optional)</em>" if fmeta.get("optional") else ""
        default = f" default: <code>{fmeta['default']}</code>" if "default" in fmeta else ""
        desc = fmeta.get("description", fmeta.get("value", ""))
        sub_fields_rows += (
            f"<tr><td><code>{fname}</code></td>"
            f"<td><code>{fmeta['type']}</code>{opt}{default}</td>"
            f"<td>{desc}</td></tr>\n"
        )

    # Server messages
    sched_msg = messages["server_to_client"]["schedule"]

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{info['title']}</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    max-width: 900px; margin: 0 auto; padding: 40px 20px 80px;
    color: #1a202c; line-height: 1.6; background: #fafafa;
  }}
  h1 {{ font-size: 28px; margin-bottom: 4px; }}
  h2 {{ font-size: 20px; margin: 36px 0 12px; padding-bottom: 6px;
        border-bottom: 2px solid #f58220; color: #333; }}
  h3 {{ font-size: 16px; margin: 20px 0 8px; color: #555; }}
  p {{ margin-bottom: 12px; color: #444; }}
  .subtitle {{ color: #888; font-size: 14px; margin-bottom: 24px; }}
  .badge {{ display: inline-block; background: #f58220; color: #fff;
            padding: 2px 8px; border-radius: 4px; font-size: 12px;
            font-weight: 600; margin-right: 6px; }}
  .badge.ws {{ background: #2563eb; }}
  .badge.http {{ background: #059669; }}
  a {{ color: #f58220; }}
  table {{ width: 100%; border-collapse: collapse; margin: 12px 0 20px;
           font-size: 14px; }}
  th, td {{ text-align: left; padding: 8px 12px; border: 1px solid #e2e8f0; }}
  th {{ background: #f1f5f9; font-weight: 600; color: #333; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
  code {{ background: #f1f5f9; padding: 1px 5px; border-radius: 3px;
          font-size: 13px; }}
  pre {{ background: #1e293b; color: #e2e8f0; padding: 16px; border-radius: 8px;
         overflow-x: auto; font-size: 13px; line-height: 1.5; margin: 12px 0 20px; }}
  pre code {{ background: none; padding: 0; color: inherit; }}
  .config-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px 24px;
                   margin: 12px 0 20px; }}
  .config-item {{ font-size: 14px; }}
  .config-item .label {{ color: #888; }}
  .config-item .value {{ font-weight: 600; }}
  .raw-link {{ float: right; font-size: 13px; color: #888; }}
  .raw-link:hover {{ color: #f58220; }}
  .nav {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0 8px; }}
  .nav a {{ font-size: 13px; color: #666; text-decoration: none;
            padding: 4px 10px; border-radius: 4px; background: #e2e8f0; }}
  .nav a:hover {{ background: #f58220; color: #fff; }}
  .seq {{ margin: 20px 0; overflow-x: auto; }}
  .seq pre {{ background: #1e293b; color: #e2e8f0; padding: 24px; border-radius: 8px;
              font-size: 13px; line-height: 1.6; white-space: pre; }}
  .seq .actor {{ color: #f58220; font-weight: 600; }}
  .seq .msg {{ color: #7dd3fc; }}
  .seq .note {{ color: #94a3b8; font-style: italic; }}
</style>
</head>
<body>

<a href="/api/spec" class="raw-link">Raw JSON &rarr;</a>
<h1>{info['title']}</h1>
<p class="subtitle">
  <span class="badge ws">WebSocket</span>
  <code>{info['websocket_url']}</code>
  &nbsp;&middot;&nbsp; v{info['version']}
</p>
<p>{info['description']}</p>

<div class="nav">
  <a href="#sequence">Sequence</a>
  <a href="#config">Config</a>
  <a href="#subscribe">Subscribe</a>
  <a href="#schedule">Schedule</a>
  <a href="#trip">Trip Type</a>
  <a href="#ferry">Ferry</a>
  <a href="#rate-limiting">Rate Limiting</a>
</div>

<h2 id="sequence">Protocol Sequence</h2>
<div class="seq"><pre>
<span class="actor">ESP32</span>                      <span class="actor">Transit Server (:8000)</span>              <span class="actor">OneBusAway API</span>
  │                              │                                  │
  │──── WebSocket connect ──────▶│                                  │
  │                              │                                  │
  │──── <span class="msg">schedule:subscribe</span> ────▶│                                  │
  │     routeStopPairs=          │                                  │
  │     "40_100240,1_8494,0;     │                                  │
  │      95_73,95_3,0"           │                                  │
  │                              │                                  │
  │                              │──── GET /arrivals-for-stop ─────▶│
  │                              │                                  │
  │                              │◀─── arrivals JSON ──────────────│
  │                              │                                  │
  │                              │  <span class="note">┌─────────────────────────────┐</span>
  │                              │  <span class="note">│ Apply ferry direction filter │</span>
  │                              │  <span class="note">│ Map vessel names (vehicleId) │</span>
  │                              │  <span class="note">│ Apply route abbreviations    │</span>
  │                              │  <span class="note">│ Apply time offsets            │</span>
  │                              │  <span class="note">│ Sort by arrivalTime           │</span>
  │                              │  <span class="note">└─────────────────────────────┘</span>
  │                              │                                  │
  │◀──── <span class="msg">schedule</span> ──────────────│                                  │
  │      trips: [...]            │                                  │
  │                              │                                  │
  │  <span class="note">display_mins =</span>              │        <span class="note">every {config['check_interval_seconds']}s</span>
  │  <span class="note">(arrivalTime - now) / 60</span>     │──── GET /arrivals-for-stop ─────▶│
  │                              │◀─── arrivals JSON ──────────────│
  │◀──── <span class="msg">schedule</span> ──────────────│                                  │
  │                              │                                  │
  │◀──── <span class="msg">heartbeat</span> ─────────────│        <span class="note">every 10s</span>
  │                              │                                  │
</pre></div>

<h2 id="config">Current Configuration</h2>
<div class="config-grid">
  <div class="config-item"><span class="label">Check interval:</span>
    <span class="value">{config['check_interval_seconds']}s</span></div>
  <div class="config-item"><span class="label">Arrival threshold:</span>
    <span class="value">{config['arrival_threshold_minutes']} min</span></div>
  <div class="config-item"><span class="label">Time display:</span>
    <span class="value">{config['time_display']}</span></div>
  <div class="config-item"><span class="label">Panels:</span>
    <span class="value">{config['num_panels']} &times; {config['panel_size']}</span></div>
  <div class="config-item"><span class="label">Scroll headsigns:</span>
    <span class="value">{config['scroll_headsigns']}</span></div>
</div>

<h3>Subscriptions</h3>
<table>
<tr><th>Feed</th><th>Route</th><th>Stop</th><th>Label</th><th>Offset</th></tr>
{sub_rows}</table>

<h2 id="subscribe">Client &rarr; Server: <code>schedule:subscribe</code></h2>
<p>{sub_msg['description']}</p>
<table>
<tr><th>Field</th><th>Type</th><th>Description</th></tr>
{sub_fields_rows}</table>

<h3>Example</h3>
<pre><code>{json_block(sub_msg['example'])}</code></pre>

<h2 id="schedule">Server &rarr; Client: <code>schedule</code></h2>
<p>{sched_msg['description']}</p>

<h3>Bus Example</h3>
<pre><code>{json_block(sched_msg['examples']['bus'])}</code></pre>

<h3>Ferry Example</h3>
<pre><code>{json_block(sched_msg['examples']['ferry'])}</code></pre>

<h3>Heartbeat</h3>
<pre><code>{json_block(messages['server_to_client']['heartbeat']['example'])}</code></pre>

<h2 id="trip">Trip Type</h2>
<table>
<tr><th>Field</th><th>Type</th><th>Description</th></tr>
{trip_rows}</table>

<h2>ID Prefixes</h2>
<table>
<tr><th>Prefix</th><th>Description</th></tr>
{prefix_rows}</table>

<h2 id="ferry">Ferry Support</h2>
<p><strong>Vessel mapping:</strong> {ferry.get('vessel_mapping', 'N/A')}</p>
<p><strong>Arrival vs departure:</strong> {ferry.get('arrival_vs_departure', 'N/A')}</p>
<p><strong>Direction filtering:</strong> {ferry.get('direction_filtering', 'N/A')}</p>
<p><strong>Realtime detection:</strong> {ferry.get('realtime_detection', 'N/A')}</p>

{"<h3>Route Abbreviations</h3><table><tr><th>Original</th><th>Short</th></tr>" + abbr_rows + "</table>" if abbr_rows else ""}

<h2 id="rate-limiting">Rate Limiting</h2>
<p><strong>Backoff:</strong> {rate.get('backoff', 'N/A')}</p>
<p><strong>Per-stop cooldown:</strong> {rate.get('per_stop_cooldown', 'N/A')}</p>

</body>
</html>"""


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

def resolve_route_polylines(config: TransitConfig) -> List[Dict[str, Any]]:
    """Return route polylines from local GTFS shapes for configured subscriptions.

    Each entry: {name, color, polylines: [[[lat, lon], ...], ...]}
    Falls back to empty list if GTFS data is not available.
    """
    if not _GTFS_DB.exists():
        return []

    conn = sqlite3.connect(_GTFS_DB)
    conn.row_factory = sqlite3.Row
    result = []
    seen_routes: set[str] = set()

    for sub in config.subscriptions:
        raw_route = sub.route
        # Strip agency prefix: "1_100001" -> "100001", "wsf:7" -> "7"
        if ":" in raw_route:
            raw_route = raw_route.split(":", 1)[1]
        parts = raw_route.split("_", 1)
        agency_id = parts[0] if len(parts) == 2 and parts[0].isdigit() else None
        gtfs_route_id = parts[1] if len(parts) == 2 and parts[0].isdigit() else raw_route

        if gtfs_route_id in seen_routes:
            continue
        seen_routes.add(gtfs_route_id)

        row = conn.execute(
            "SELECT short_name, color FROM routes WHERE route_id=?", (gtfs_route_id,)
        ).fetchone()
        if not row:
            continue

        # Find shape_ids for this route from the GTFS files
        if agency_id and (_GTFS_DIR / agency_id / "trips.txt").exists():
            trips_path = _GTFS_DIR / agency_id / "trips.txt"
        else:
            # Search all agencies
            trips_path = None
            for aid in ["1", "40", "95"]:
                p = _GTFS_DIR / aid / "trips.txt"
                if p.exists():
                    # Quick check if route exists in this agency
                    with open(p) as f:
                        for line in f:
                            if gtfs_route_id in line:
                                trips_path = p
                                agency_id = aid
                                break
                if trips_path:
                    break

        if not trips_path:
            continue

        shape_ids: set[str] = set()
        with open(trips_path) as f:
            for t in csv.DictReader(f):
                if t["route_id"] == gtfs_route_id and t.get("shape_id"):
                    shape_ids.add(t["shape_id"])

        if not shape_ids:
            continue

        shapes_path = _GTFS_DIR / agency_id / "shapes.txt"
        raw_shapes: dict[str, list[tuple[int, float, float]]] = {}
        with open(shapes_path) as f:
            for s in csv.DictReader(f):
                if s["shape_id"] in shape_ids:
                    raw_shapes.setdefault(s["shape_id"], []).append(
                        (int(s["shape_pt_sequence"]), float(s["shape_pt_lat"]), float(s["shape_pt_lon"]))
                    )

        polylines = [
            [[lat, lon] for _, lat, lon in sorted(pts)]
            for pts in raw_shapes.values()
        ]

        result.append({
            "name": row["short_name"] or gtfs_route_id,
            "color": f"#{row['color']}" if row["color"] else "#888888",
            "polylines": polylines,
        })

    conn.close()
    return result


def generate_walkshed_html(
    stops: List[Dict[str, Any]],
    routes: List[Dict[str, Any]] = None,
) -> str:
    """Generate a self-contained HTML walkshed map using Leaflet + OpenRouteService isochrones.

    No API token required. Isochrones are fetched from the free ORS public API.
    Route polylines come from local GTFS shapes data.
    """
    stops_json = json.dumps(stops)
    routes_json = json.dumps(routes or [])
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit Tracker — Walksheds</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
  #map {{ height: 100vh; }}
  .legend {{
    background: rgba(20,20,30,0.88); color: #e4e7eb;
    padding: 12px 16px; border-radius: 8px;
    font-size: 13px; line-height: 1.8;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }}
  .legend b {{ display: block; margin-bottom: 4px; color: #fff; }}
  .swatch {{
    display: inline-block; width: 16px; height: 10px;
    border-radius: 2px; margin-right: 6px; vertical-align: middle;
  }}
  .route-swatch {{
    display: inline-block; width: 16px; height: 3px;
    border-radius: 1px; margin-right: 6px; vertical-align: middle;
  }}
  #loading {{
    position: fixed; top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    background: rgba(20,20,30,0.9); color: #fff;
    padding: 20px 30px; border-radius: 8px;
    font-size: 16px; z-index: 9999;
  }}
</style>
</head>
<body>
<div id="map"></div>
<div id="loading">Loading walksheds...</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const STOPS = {stops_json};
const ROUTES = {routes_json};
const HIGHLIGHT = '#f58220';

const center = STOPS.length
  ? [STOPS[0].lat, STOPS[0].lon]
  : [47.6062, -122.3321];

const map = L.map('map').setView(center, 14);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 19,
}}).addTo(map);

// Fit to all stops
if (STOPS.length > 1) {{
  const bounds = L.latLngBounds(STOPS.map(s => [s.lat, s.lon]));
  map.fitBounds(bounds, {{ padding: [60, 60] }});
}}

// Draw route polylines from GTFS
ROUTES.forEach(route => {{
  route.polylines.forEach(coords => {{
    L.polyline(coords, {{
      color: route.color || '#888',
      weight: 3,
      opacity: 0.7,
    }}).addTo(map);
  }});
}});

// Legend
const legend = L.control({{ position: 'bottomright' }});
legend.onAdd = () => {{
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML =
    '<b>Walk Time</b>' +
    `<div><span class="swatch" style="background:rgba(245,130,32,0.5)"></span>5 min</div>` +
    `<div><span class="swatch" style="background:rgba(245,130,32,0.3)"></span>10 min</div>` +
    `<div><span class="swatch" style="background:rgba(245,130,32,0.15)"></span>15 min</div>`;
  if (ROUTES.length) {{
    div.innerHTML += '<b style="margin-top:8px;display:block">Routes</b>';
    ROUTES.forEach(r => {{
      div.innerHTML += `<div><span class="route-swatch" style="background:${{r.color}}"></span>${{r.name}}</div>`;
    }});
  }}
  return div;
}};
legend.addTo(map);

// Fetch isochrones from OpenRouteService (free, no token)
const CONTOURS = [
  {{ minutes: 15, fillOpacity: 0.15 }},
  {{ minutes: 10, fillOpacity: 0.30 }},
  {{ minutes: 5,  fillOpacity: 0.50 }},
];

async function loadWalkshed(stop, i) {{
  if (i > 0) await new Promise(r => setTimeout(r, 300));
  try {{
    const resp = await fetch(
      `https://api.openrouteservice.org/v2/isochrones/foot-walking`,
      {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json', 'Accept': 'application/json, application/geo+json' }},
        body: JSON.stringify({{
          locations: [[stop.lon, stop.lat]],
          range: [900, 600, 300],  // 15, 10, 5 min in seconds
          range_type: 'time',
        }}),
      }}
    );
    if (!resp.ok) throw new Error(`ORS ${{resp.status}}`);
    const data = await resp.json();

    data.features.forEach((feature, fi) => {{
      const contour = CONTOURS[fi];
      if (!contour) return;
      // GeoJSON coords are [lon, lat], Leaflet wants [lat, lon]
      const latlngs = feature.geometry.coordinates[0].map(([lon, lat]) => [lat, lon]);
      L.polygon(latlngs, {{
        color: HIGHLIGHT,
        fillColor: HIGHLIGHT,
        weight: 1.5,
        opacity: 0.6,
        fillOpacity: contour.fillOpacity,
      }}).addTo(map);
    }});
  }} catch (err) {{
    console.warn(`Walkshed failed for ${{stop.name}}:`, err);
    // Fallback: draw approximate circles (400m ≈ 5min walk)
    [900, 600, 300].forEach((secs, fi) => {{
      L.circle([stop.lat, stop.lon], {{
        radius: secs * 1.2,  // ~1.2 m/s walking
        color: HIGHLIGHT,
        fillColor: HIGHLIGHT,
        weight: 1,
        opacity: 0.4,
        fillOpacity: CONTOURS[fi].fillOpacity * 0.6,
      }}).addTo(map);
    }});
  }}

  L.marker([stop.lat, stop.lon])
    .bindPopup(`<b>${{stop.label}}</b><br>ID: ${{stop.stop_id}}`)
    .addTo(map);

  document.getElementById('loading').textContent =
    `Loading walksheds... (${{i + 1}}/${{STOPS.length}})`;
}}

(async () => {{
  for (let i = 0; i < STOPS.length; i++) {{
    await loadWalkshed(STOPS[i], i);
  }}
  document.getElementById('loading').style.display = 'none';
}})();
</script>
</body>
</html>"""


def generate_simulator_html(config: TransitConfig) -> str:
    """Generate a self-contained web LED matrix simulator page."""
    pairs = []
    for sub in config.subscriptions:
        r_id = sub.route if ":" in sub.route else f"{sub.feed}:{sub.route}"
        s_id = sub.stop if ":" in sub.stop else f"{sub.feed}:{sub.stop}"
        off_sec = 0
        match = re.search(r"(-?\d+)", str(sub.time_offset))
        if match:
            off_sec = int(match.group(1)) * 60
        pairs.append(f"{r_id},{s_id},{off_sec}")
    pairs_str = ";".join(pairs)

    api_url = config.api_url
    if (
        config.use_local_api
        and "localhost" not in api_url
        and "127.0.0.1" not in api_url
    ):
        api_url = "ws://localhost:8000"

    display_width = config.panel_width * config.num_panels
    display_height = 32
    pixel_scale = 7

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit Tracker — LED Simulator</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #111; color: #e4e7eb;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; min-height: 100vh; gap: 16px;
  }}
  canvas {{ border-radius: 8px; box-shadow: 0 0 40px rgba(0,0,0,0.5); }}
  .status {{ font-size: 13px; color: #888; display: flex; gap: 12px; align-items: center; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 4px; }}
  .dot.connected {{ background: #4ade80; }}
  .dot.disconnected {{ background: #ef4444; }}
  .dot.connecting {{ background: #facc15; }}
  h1 {{ font-size: 16px; font-weight: 600; color: #999; letter-spacing: 1px; text-transform: uppercase; }}
</style>
</head>
<body>
<h1>HUB75 {display_width}x{display_height} LED Simulator</h1>
<canvas id="led" width="{display_width * pixel_scale}" height="{display_height * pixel_scale}"></canvas>
<div class="status">
  <span><span id="statusDot" class="dot connecting"></span><span id="statusText">Connecting...</span></span>
  <span id="stopInfo"></span>
</div>
<script>
const CONFIG = {{
  apiUrl: {json.dumps(api_url)},
  pairsStr: {json.dumps(pairs_str)},
  displayWidth: {display_width},
  displayHeight: {display_height},
  pixelScale: {pixel_scale},
}};
const GLYPHS={{'0':[0x0E,0x11,0x11,0x11,0x11,0x11,0x0E],'1':[0x04,0x0C,0x04,0x04,0x04,0x04,0x0E],'2':[0x0E,0x11,0x01,0x02,0x04,0x08,0x1F],'3':[0x1F,0x02,0x04,0x02,0x01,0x11,0x0E],'4':[0x02,0x06,0x0A,0x12,0x1F,0x02,0x02],'5':[0x1F,0x10,0x1E,0x01,0x01,0x11,0x0E],'6':[0x0E,0x10,0x10,0x1E,0x11,0x11,0x0E],'7':[0x1F,0x01,0x02,0x04,0x08,0x08,0x08],'8':[0x0E,0x11,0x11,0x0E,0x11,0x11,0x0E],'9':[0x0E,0x11,0x11,0x0F,0x01,0x02,0x0C],'A':[0x04,0x0A,0x11,0x11,0x1F,0x11,0x11],'B':[0x1E,0x11,0x11,0x1E,0x11,0x11,0x1E],'C':[0x0E,0x11,0x10,0x10,0x10,0x11,0x0E],'D':[0x1C,0x12,0x11,0x11,0x11,0x12,0x1C],'E':[0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F],'F':[0x1F,0x10,0x10,0x1E,0x10,0x10,0x10],'G':[0x0E,0x11,0x10,0x17,0x11,0x11,0x0F],'H':[0x11,0x11,0x11,0x1F,0x11,0x11,0x11],'I':[0x0E,0x04,0x04,0x04,0x04,0x04,0x0E],'J':[0x07,0x02,0x02,0x02,0x02,0x12,0x0C],'K':[0x11,0x12,0x14,0x18,0x14,0x12,0x11],'L':[0x10,0x10,0x10,0x10,0x10,0x10,0x1F],'M':[0x11,0x1B,0x15,0x11,0x11,0x11,0x11],'N':[0x11,0x11,0x19,0x15,0x13,0x11,0x11],'O':[0x0E,0x11,0x11,0x11,0x11,0x11,0x0E],'P':[0x1E,0x11,0x11,0x1E,0x10,0x10,0x10],'Q':[0x0E,0x11,0x11,0x11,0x15,0x12,0x0D],'R':[0x1E,0x11,0x11,0x1E,0x14,0x12,0x11],'S':[0x0E,0x11,0x10,0x0E,0x01,0x11,0x0E],'T':[0x1F,0x04,0x04,0x04,0x04,0x04,0x04],'U':[0x11,0x11,0x11,0x11,0x11,0x11,0x0E],'V':[0x11,0x11,0x11,0x11,0x11,0x0A,0x04],'W':[0x11,0x11,0x11,0x15,0x15,0x1B,0x11],'X':[0x11,0x11,0x0A,0x04,0x0A,0x11,0x11],'Y':[0x11,0x11,0x0A,0x04,0x04,0x04,0x04],'Z':[0x1F,0x01,0x02,0x04,0x08,0x10,0x1F],' ':[0,0,0,0,0,0,0],'m':[0,0,0x1A,0x15,0x15,0x15,0x15],'.':[0,0,0,0,0,0,0x04],'-':[0,0,0,0x1F,0,0,0],'>':[0x10,0x08,0x04,0x08,0x10,0,0],'(':[0x04,0x08,0x08,0x08,0x08,0x08,0x04],')':[0x08,0x04,0x04,0x04,0x04,0x04,0x08],'/':[0x01,0x02,0x04,0x08,0x10,0,0],'?':[0x0E,0x11,0x01,0x02,0x04,0,0x04]}};
const REALTIME_ICON=[[0,0,0,3,3,3],[0,0,3,0,0,0],[0,3,0,0,2,2],[3,0,0,2,0,0],[3,0,2,0,0,1],[3,0,2,0,1,1]];
const COLORS={{yellow:'#FFD700',hot_pink:'#FF69B4',white:'#FFFFFF',bright_blue:'#5B9BD5',grey74:'#BDBDBD',cyan:'#00CED1'}};
function getBitmap(t){{const rows=[[],[],[],[],[],[],[]];for(const ch of t){{const g=GLYPHS[ch.toUpperCase()]||GLYPHS['?'];for(let r=0;r<7;r++){{for(let b=4;b>=0;b--)rows[r].push((g[r]>>b)&1);rows[r].push(0);}}}}return rows;}}
function getLiveIconFrame(e){{const c=e%4000;let f=0;if(c>=3000)f=Math.min(Math.floor((c-3000)/200)+1,5);const rows=[[],[],[],[],[],[],[]];for(let r=0;r<6;r++){{for(let c2=0;c2<6;c2++){{const s=REALTIME_ICON[r][c2];if(s===0){{rows[r].push(0);continue;}}let lit=false;if(s===1&&[1,2,3].includes(f))lit=true;else if(s===2&&[2,3,4].includes(f))lit=true;else if(s===3&&[3,4,5].includes(f))lit=true;rows[r].push(lit?2:1);}}}}rows[6]=[0,0,0,0,0,0];return rows;}}
function resolveColor(c){{if(!c)return COLORS.yellow;if(c.startsWith('#'))return c;return COLORS[c]||COLORS.yellow;}}
let trips=[],startTime=performance.now(),wsStatus='connecting';
function connectWS(){{wsStatus='connecting';updateStatus();const ws=new WebSocket(CONFIG.apiUrl);ws.onopen=()=>{{wsStatus='connected';updateStatus();ws.send(JSON.stringify({{event:'schedule:subscribe',client_name:'Web Simulator',data:{{routeStopPairs:CONFIG.pairsStr,limit:10}}}}))}};ws.onmessage=(e)=>{{const d=JSON.parse(e.data);if(d.event==='schedule'){{const p=d.payload||d.data||{{}};trips=p.trips||[];}}}};ws.onclose=()=>{{wsStatus='disconnected';updateStatus();setTimeout(connectWS,5000)}};ws.onerror=()=>ws.close();}}
function updateStatus(){{const dot=document.getElementById('statusDot'),text=document.getElementById('statusText');dot.className='dot '+wsStatus;text.textContent=wsStatus.charAt(0).toUpperCase()+wsStatus.slice(1);}}
function getUpcomingDepartures(){{const nowMs=Date.now(),deps=[];for(const trip of trips){{let a=trip.arrivalTime||trip.predictedArrivalTime||trip.scheduledArrivalTime;if(!a)continue;if(a<1e12)a*=1000;const diff=Math.floor((a-nowMs)/60000);if(diff<-1)continue;const rn=trip.routeName||'';let color='yellow';if(rn.includes('14'))color='hot_pink';else if(trip.routeColor)color='#'+trip.routeColor;deps.push({{tripId:trip.tripId,diff:Math.max(0,diff),route:rn,headsign:trip.headsign||'Transit',color,live:!!trip.isRealtime,stopId:trip.stopId}});}}deps.sort((a,b)=>a.diff-b.diff);const limit=3,final=[],seenStops=new Set();for(const d of deps){{if(!seenStops.has(d.stopId)){{final.push(d);seenStops.add(d.stopId);}}if(final.length>=limit)break;}}if(final.length<limit)for(const d of deps){{if(!final.includes(d))final.push(d);if(final.length>=limit)break;}}final.sort((a,b)=>a.diff-b.diff);return final;}}
const canvas=document.getElementById('led'),ctx=canvas.getContext('2d');
const W=CONFIG.displayWidth,H=CONFIG.displayHeight,S=CONFIG.pixelScale,LED_RADIUS=S*0.35,DIM='#1a1a2e';
function renderFrame(){{const elapsed=performance.now()-startTime;ctx.fillStyle='#0a0a0f';ctx.fillRect(0,0,canvas.width,canvas.height);const pixels=Array.from({{length:H}},()=>Array(W).fill(null));const deps=getUpcomingDepartures();if(deps.length===0){{const bm=getBitmap(trips.length===0?'Connecting...':'No Buses');for(let r=0;r<7&&r<bm.length;r++)for(let c=0;c<bm[r].length&&c<W;c++)if(bm[r][c])pixels[r][c]=COLORS.cyan;}}else{{let y=0;for(let di=0;di<deps.length&&di<3;di++){{renderTripRow(pixels,deps[di],elapsed,y);y+=8;}}}}for(let r=0;r<H;r++)for(let c=0;c<W;c++){{const cx=c*S+S/2,cy=r*S+S/2;ctx.beginPath();ctx.arc(cx,cy,LED_RADIUS,0,Math.PI*2);ctx.fillStyle=pixels[r][c]||DIM;ctx.fill();}}requestAnimationFrame(renderFrame);}}
function renderTripRow(pixels,dep,elapsed,yOff){{const routeBm=getBitmap(dep.route),routeW=routeBm[0].length,timeText=dep.diff<=0?'Now':dep.diff+'m',timeBm=getBitmap(timeText),timeW=timeBm[0].length,iconW=dep.live?6:0,hsXStart=routeW+3,hsAreaW=W-hsXStart-timeW-(dep.live?iconW+2:0),hsBmFull=getBitmap(dep.headsign),hsFullW=hsBmFull[0].length;let scrollOff=0;if(hsFullW>hsAreaW){{const overflow=hsFullW-hsAreaW,scrollSpeed=100,waitMs=2000,scrollDur=overflow*scrollSpeed,totalCycle=(waitMs+scrollDur)*2,pos=elapsed%totalCycle;if(pos<waitMs)scrollOff=0;else if(pos<waitMs+scrollDur)scrollOff=Math.floor((pos-waitMs)/scrollSpeed);else if(pos<waitMs*2+scrollDur)scrollOff=overflow;else scrollOff=overflow-Math.floor((pos-waitMs*2-scrollDur)/scrollSpeed);scrollOff=Math.max(0,Math.min(overflow,scrollOff));}}const routeColor=resolveColor(dep.color),timeColor=dep.live?COLORS.bright_blue:COLORS.grey74;for(let r=0;r<7;r++){{for(let c=0;c<routeW&&c<W;c++)if(routeBm[r][c])pixels[yOff+r][c]=routeColor;}}const timeX=W-timeW;for(let r=0;r<7;r++)for(let c=0;c<timeW;c++){{const tx=timeX+c;if(tx>=0&&tx<W&&timeBm[r][c])pixels[yOff+r][tx]=timeColor;}}if(dep.live){{const iconBm=getLiveIconFrame(elapsed),iconX=timeX-8;for(let r=0;r<6;r++)for(let c=0;c<6;c++){{const ix=iconX+c;if(ix>=0&&ix<W){{const v=iconBm[r][c];if(v===2)pixels[yOff+r][ix]=COLORS.white;else if(v===1)pixels[yOff+r][ix]=COLORS.bright_blue;}}}}}}for(let r=0;r<7;r++)for(let c=0;c<hsAreaW;c++){{const srcC=c+scrollOff,destX=hsXStart+c;if(destX<W&&srcC<hsFullW&&hsBmFull[r][srcC])pixels[yOff+r][destX]=COLORS.white;}}}}
connectWS();
requestAnimationFrame(renderFrame);
</script>
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
    """Start the Transit Tracker web server."""
    if port is None:
        port = int(os.environ.get("PORT", 8080))

    print("[WEB] Resolving stop coordinates...")
    stops = await resolve_stop_coordinates(config)
    if not stops:
        print("[WEB] No stops found in config. Add stops first with 'transit-tracker'.")
        return
    print(f"[WEB] Resolved {len(stops)} stops")

    print("[WEB] Loading route polylines from GTFS...")
    routes = resolve_route_polylines(config)
    print(f"[WEB] Loaded {len(routes)} route shapes")

    spec_json = generate_api_spec(config)
    spec_html = generate_spec_html(spec_json)
    simulator_html = generate_simulator_html(config)
    walkshed_html = generate_walkshed_html(stops, routes)
    stops_json = json.dumps(stops, indent=2)

    pages = [
        {"path": "/walkshed", "name": "Walksheds", "description": "Walking distance isochrone map with route lines"},
        {"path": "/simulator", "name": "LED Simulator", "description": "Web-based HUB75 LED matrix simulator"},
        {"path": "/spec", "name": "API Docs", "description": "Interactive WebSocket API documentation"},
        {"path": "/api/spec", "name": "API Spec (JSON)", "description": "Raw JSON specification with example payloads"},
        {"path": "/api/stops", "name": "Stops", "description": "Configured stop coordinates as JSON"},
    ]
    index_html = generate_index_html(pages)

    TransitWebHandler.routes = {
        "/": index_html,
        "/walkshed": walkshed_html,
        "/simulator": simulator_html,
        "/spec": spec_html,
        "/api/spec": spec_json,
        "/api/stops": stops_json,
    }

    server = HTTPServer((host, port), TransitWebHandler)
    print(f"[WEB] Transit Tracker web server at http://{host}:{port}")
    print("[WEB]   /walkshed   — Walking distance isochrone map")
    print("[WEB]   /simulator  — LED matrix simulator")
    print("[WEB]   /spec       — API documentation page")
    print("[WEB]   /api/spec   — Raw JSON specification")
    print("[WEB]   /api/stops  — Stop coordinates JSON")
    print("[WEB] Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[WEB] Shutting down...")
        server.shutdown()

