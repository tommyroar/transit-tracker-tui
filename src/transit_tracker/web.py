import asyncio
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

import websockets

from .config import TransitConfig
from .logging import get_logger
from .metrics import metrics
from .transit_api import TransitAPI

log = get_logger("transit_tracker.web")


# -- Shared API helpers (used by both legacy HTTPServer and websockets paths) --

def _handle_profiles_list() -> dict:
    """Return profiles list and active profile as a dict."""
    from .config import get_last_config_path, list_profiles
    active = get_last_config_path()
    profiles = [
        {"name": os.path.basename(p), "path": p, "active": p == active}
        for p in list_profiles()
    ]
    return {"profiles": profiles, "active": active}


def _handle_profile_activate(query: dict) -> tuple:
    """Activate a profile by name. Returns (status_code, response_dict)."""
    from .config import list_profiles, set_last_config_path
    name = query.get("name", [None])[0]
    if not name:
        return (400, {"error": "Missing 'name' query parameter"})
    all_profiles = list_profiles()
    match = next((p for p in all_profiles if os.path.basename(p) == name), None)
    if not match:
        available = [os.path.basename(p) for p in all_profiles]
        return (404, {"error": f"Profile '{name}' not found", "available": available})
    log.info("REST profile switch to %s", name, extra={"component": "web", "profile": match})
    set_last_config_path(match)
    return (200, {"status": "ok", "profile": name, "path": match,
                  "message": "Profile activated. Server will hot-reload within 30 seconds."})


def _handle_dimming_set(query: dict) -> tuple:
    """Update dimming settings from query params. Returns (status_code, response_dict)."""
    from .config import DimmingEntry, load_service_settings, save_service_settings
    log.info("REST dimming update: %s", {k: v for k, v in query.items() if k != "device_ip"},
             extra={"component": "web"})
    settings = load_service_settings()
    raw_entries = query.get("schedule", [])
    if raw_entries:
        entries = []
        for entry in raw_entries:
            time_str, brightness_str = entry.split(",", 1)
            entries.append(DimmingEntry(time=time_str.strip(), brightness=int(brightness_str.strip())))
        settings.dimming_schedule = entries
    if "brightness" in query:
        settings.display_brightness = int(query["brightness"][0])
    if "device_ip" in query:
        settings.device_ip = query["device_ip"][0]
    save_service_settings(settings)
    return (200, {
        "status": "ok",
        "dimming_schedule": [e.model_dump() for e in settings.dimming_schedule],
        "display_brightness": settings.display_brightness,
        "device_ip": settings.device_ip,
        "message": "Dimming settings saved. Will take effect within 60 seconds.",
    })


async def resolve_stop_coordinates(config: TransitConfig) -> List[Dict[str, Any]]:
    """Fetch lat/lon for all configured stops from the OBA API."""
    api = TransitAPI(oba_api_key=config.service.oba_api_key)
    try:
        tasks = [api.get_stop(stop.stop_id) for stop in config.transit_tracker.stops]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        stops = []
        for stop_cfg, result in zip(config.transit_tracker.stops, results, strict=True):
            if isinstance(result, Exception):
                log.warning("Could not fetch stop %s: %s", stop_cfg.stop_id, result, extra={"component": "web"})
                continue
            if result is None:
                log.warning("Stop %s not found", stop_cfg.stop_id, extra={"component": "web"})
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
            "check_interval_seconds": config.service.check_interval_seconds,
            "arrival_threshold_minutes": config.service.arrival_threshold_minutes,
            "time_display": config.transit_tracker.time_display,
            "num_panels": config.service.num_panels,
            "panel_size": f"{config.service.panel_width}x{config.service.panel_height}",
            "scroll_headsigns": config.transit_tracker.scroll_headsigns,
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
                        f"(every {config.service.check_interval_seconds}s)."
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
    cards = "".join(
        '<a href="' + p["path"] + '" class="card"><h2>' + p["name"] + '</h2><p>' + p["description"] + '</p></a>'
        for p in pages
    )
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Transit Tracker</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect width='10' height='3' rx='1.5' fill='%23e8a830'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root { --bg:#06080e;--bg1:#0c0f1a;--border:#1a1e35;--text0:#eae8e4;--text2:#5c6080;--amber:#e8a830;--amber-bg:rgba(232,168,48,0.08); }
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Outfit',system-ui,sans-serif;background:var(--bg);color:var(--text0);display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:40px 20px}
body::after{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E");opacity:0.018;pointer-events:none;z-index:9999}
.brand{font-weight:700;font-size:12px;letter-spacing:2.5px;text-transform:uppercase;color:var(--amber);display:flex;align-items:center;gap:10px;margin-bottom:6px}
.brand svg{opacity:0.8}
p.sub{color:var(--text2);font-size:13px;margin-bottom:28px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;max-width:600px;width:100%}
.card{display:block;text-decoration:none;color:var(--text0);background:var(--bg1);border:1px solid var(--border);border-radius:8px;padding:14px 16px;transition:border-color 0.2s,transform 0.15s}
.card:hover{border-color:var(--amber);transform:translateY(-2px)}
.card h2{font-size:14px;font-weight:600;margin-bottom:3px;color:var(--amber)}
.card p{font-size:11.5px;color:var(--text2);line-height:1.4}
@keyframes fi{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.card{animation:fi 0.4s cubic-bezier(0.22,1,0.36,1) both}
.card:nth-child(1){animation-delay:.04s}.card:nth-child(2){animation-delay:.08s}.card:nth-child(3){animation-delay:.12s}
.card:nth-child(4){animation-delay:.16s}.card:nth-child(5){animation-delay:.2s}.card:nth-child(6){animation-delay:.24s}
.card:nth-child(7){animation-delay:.28s}.card:nth-child(8){animation-delay:.32s}.card:nth-child(9){animation-delay:.36s}
</style>
</head>
<body>
  <div class="brand">
    <svg width="18" height="13" viewBox="0 0 18 13" fill="none"><rect width="18" height="2.4" rx="1.2" fill="currentColor"/><rect y="5" width="12" height="2.4" rx="1.2" fill="currentColor" opacity="0.55"/><rect y="10" width="7" height="2.4" rx="1.2" fill="currentColor" opacity="0.25"/></svg>
    Transit Tracker
  </div>
  <p class="sub">Available pages</p>
  <div class="grid">""" + cards + """</div>
</body>
</html>"""


class TransitWebHandler(BaseHTTPRequestHandler):
    """HTTP request handler with a routes dict for extensibility."""

    routes: Dict[str, str] = {}
    dynamic_routes: set = set()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        # Dynamic routes (read fresh on each request)
        if path in self.dynamic_routes:
            if path == "/api/status":
                self._serve_status(query)
                return
            if path == "/api/metrics":
                self._serve_metrics(query)
                return
            if path == "/api/logs":
                self._serve_logs(query)
                return
            if path == "/dashboard":
                self._serve_dashboard()
                return
            if path == "/monitor":
                self._serve_monitor()
                return
            if path == "/api/dimming":
                self._serve_dimming_get()
                return
            if path == "/api/dimming/set":
                try:
                    status, resp = _handle_dimming_set(query)
                    self._json_response(json.dumps(resp), status)
                except Exception as e:
                    self._json_error(400, str(e))
                return
            if path == "/simulator":
                self._serve_simulator()
                return
            if path == "/api/profiles":
                self._json_response(json.dumps(_handle_profiles_list()))
                return
            if path == "/api/profile/activate":
                status, resp = _handle_profile_activate(query)
                self._json_response(json.dumps(resp), status)
                return

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

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/dimming":
            self._handle_dimming_post()
            return

        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h1>404 Not Found</h1>")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        log.debug("%s", args[0], extra={"component": "web"})

    def _json_response(self, body: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _serve_status(self, query: dict = None):
        """Serve live service state from the shared state file."""
        from .network.websocket_server import SERVICE_STATE_FILE
        query = query or {}
        include_full = query.get("full", ["0"])[0] == "1"
        try:
            if os.path.exists(SERVICE_STATE_FILE):
                with open(SERVICE_STATE_FILE, "r") as f:
                    state = json.load(f)
                if not include_full:
                    # Strip last_message to keep the response lean
                    state.pop("last_message", None)
                body = json.dumps(state)
            else:
                body = json.dumps({"status": "unavailable"})
        except Exception:
            body = json.dumps({"status": "error"})
        self._json_response(body)

    def _serve_metrics(self, query: dict):
        """Serve metrics snapshot with optional time-series windowing."""
        since = float(query.get("since", [0])[0])
        body = json.dumps(metrics.snapshot(series_since=since))
        self._json_response(body)

    def _serve_logs(self, query: dict):
        """Serve recent log entries from the in-memory ring buffer."""
        since = float(query.get("since", [0])[0])
        limit = int(query.get("limit", [200])[0])
        entries = metrics.logs.snapshot(since=since, limit=limit)
        self._json_response(json.dumps({"logs": entries}))

    def _serve_dashboard(self):
        """Serve the observability dashboard HTML."""
        html = generate_dashboard_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _serve_monitor(self):
        """Serve the live network topology monitor HTML."""
        html = generate_monitor_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _json_error(self, code: int, message: str):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode("utf-8"))

    def _serve_dimming_get(self):
        """Return the current dimming schedule from service settings."""
        from .config import load_service_settings
        settings = load_service_settings()
        self._json_response(json.dumps({
            "dimming_schedule": [e.model_dump() for e in settings.dimming_schedule],
            "display_brightness": settings.display_brightness,
            "device_ip": settings.device_ip,
        }))

    def _handle_dimming_post(self):
        """Update the dimming schedule via REST, persisting to service.yaml."""
        from .config import DimmingEntry, load_service_settings, save_service_settings

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._json_error(400, f"Invalid JSON: {e}")
            return

        try:
            entries = [DimmingEntry.model_validate(e) for e in data.get("dimming_schedule", [])]
        except Exception as e:
            self._json_error(400, f"Validation error: {e}")
            return

        settings = load_service_settings()
        settings.dimming_schedule = entries
        if "device_ip" in data:
            settings.device_ip = data["device_ip"]
        if "display_brightness" in data:
            settings.display_brightness = int(data["display_brightness"])
        save_service_settings(settings)

        self._json_response(json.dumps({
            "status": "ok",
            "dimming_schedule": [e.model_dump() for e in entries],
            "message": "Schedule saved. Will take effect within 60 seconds.",
        }))

    def _serve_simulator(self):
        """Serve the web LED simulator HTML."""
        html = generate_simulator_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


async def run_web(config: TransitConfig, host: str = "0.0.0.0", port: int = None):
    """Start the Transit Tracker web server with API spec and stop data."""
    if port is None:
        port = int(os.environ.get("PORT", 8080))

    log.info("Resolving stop coordinates...", extra={"component": "web"})
    stops = await resolve_stop_coordinates(config)
    if not stops:
        log.warning("No stops resolved — serving with empty stop data", extra={"component": "web"})
        stops = []
    else:
        log.info("Resolved %d stops", len(stops), extra={"component": "web"})

    stops_json = json.dumps(stops, indent=2)
    spec_json = generate_api_spec(config)
    spec_html = generate_spec_html(spec_json)

    pages = [
        {
            "path": "/simulator",
            "name": "LED Simulator",
            "description": "Browser-based HUB75 LED matrix emulator with live WebSocket data",
        },
        {
            "path": "/dashboard",
            "name": "Dashboard",
            "description": "Live metrics and observability dashboard",
        },
        {
            "path": "/monitor",
            "name": "Network Monitor",
            "description": "Live topology diagram showing proxy, provider, and connected displays",
        },
        {
            "path": "/spec",
            "name": "API Docs",
            "description": "Interactive WebSocket API documentation",
        },
        {
            "path": "/api/spec",
            "name": "API Spec (JSON)",
            "description": "Raw JSON specification with example payloads",
        },
        {
            "path": "/api/stops",
            "name": "Stops",
            "description": "Configured stop coordinates as JSON",
        },
        {
            "path": "/api/status",
            "name": "Status",
            "description": "Live service state (clients, rate limits, uptime)",
        },
        {
            "path": "/api/metrics",
            "name": "Metrics",
            "description": "Time-series metrics snapshot (JSON)",
        },
        {
            "path": "/api/logs",
            "name": "Logs",
            "description": "Recent log entries from ring buffer (JSON)",
        },
    ]
    index_html = generate_index_html(pages)

    # -- Route tables for the dual HTTP+WS server --
    static_routes = {
        "/": index_html,
        "/spec": spec_html,
        "/api/spec": spec_json,
        "/api/stops": stops_json,
    }
    dynamic_routes = {
        "/api/status", "/api/metrics", "/api/logs", "/api/dimming",
        "/api/dimming/set",
        "/api/profiles", "/api/profile/activate",
        "/dashboard", "/monitor", "/simulator",
    }

    # -- Also configure the legacy HTTPServer routes (used by tests) --
    TransitWebHandler.routes = static_routes
    TransitWebHandler.dynamic_routes = dynamic_routes

    def _serve_dynamic(path: str, query: dict) -> tuple:
        """Serve a dynamic route, returning (status, content_type, body)."""
        from .network.websocket_server import SERVICE_STATE_FILE

        if path == "/api/status":
            include_full = query.get("full", ["0"])[0] == "1"
            try:
                if os.path.exists(SERVICE_STATE_FILE):
                    with open(SERVICE_STATE_FILE, "r") as f:
                        state = json.load(f)
                    if not include_full:
                        state.pop("last_message", None)
                    return (200, "application/json", json.dumps(state))
                return (200, "application/json", json.dumps({"status": "unavailable"}))
            except Exception:
                return (200, "application/json", json.dumps({"status": "error"}))

        if path == "/api/metrics":
            since = float(query.get("since", [0])[0])
            return (200, "application/json", json.dumps(metrics.snapshot(series_since=since)))

        if path == "/api/logs":
            since = float(query.get("since", [0])[0])
            limit = int(query.get("limit", [200])[0])
            entries = metrics.logs.snapshot(since=since, limit=limit)
            return (200, "application/json", json.dumps({"logs": entries}))

        if path == "/api/dimming":
            from .config import load_service_settings
            settings = load_service_settings()
            return (200, "application/json", json.dumps({
                "dimming_schedule": [e.model_dump() for e in settings.dimming_schedule],
                "display_brightness": settings.display_brightness,
                "device_ip": settings.device_ip,
            }))

        if path == "/api/dimming/set":
            try:
                status, resp = _handle_dimming_set(query)
                return (status, "application/json", json.dumps(resp))
            except Exception as e:
                return (400, "application/json", json.dumps({"error": str(e)}))

        if path == "/api/profiles":
            return (200, "application/json", json.dumps(_handle_profiles_list()))

        if path == "/api/profile/activate":
            status, resp = _handle_profile_activate(query)
            return (status, "application/json", json.dumps(resp))

        if path == "/dashboard":
            return (200, "text/html", generate_dashboard_html())
        if path == "/monitor":
            return (200, "text/html", generate_monitor_html())
        if path == "/simulator":
            return (200, "text/html", generate_simulator_html())

        return (404, "text/html", "<h1>404 Not Found</h1>")

    def handle_http(path: str, query: dict) -> tuple:
        """Return (status, headers_list, body_bytes) for an HTTP request."""
        clean = path.rstrip("/") or "/"
        headers = [("Access-Control-Allow-Origin", "*")]

        if clean in dynamic_routes:
            status, ct, body = _serve_dynamic(clean, query)
            headers.append(("Content-Type", f"{ct}; charset=utf-8"))
            if ct == "text/html":
                headers.append(("Cache-Control", "no-cache"))
            return (status, headers, body.encode("utf-8"))

        content = static_routes.get(clean)
        if content is not None:
            ct = "application/json" if clean.startswith("/api/") else "text/html"
            headers.append(("Content-Type", f"{ct}; charset=utf-8"))
            return (200, headers, content.encode("utf-8"))

        headers.append(("Content-Type", "text/html; charset=utf-8"))
        return (404, headers, b"<h1>404 Not Found</h1>")

    # -- WebSocket proxy: relay /ws connections to the internal WS server --
    async def ws_proxy_handler(ws):
        """Proxy a WebSocket connection to the internal server on :8000."""
        try:
            async with websockets.connect("ws://localhost:8000") as upstream:
                async def client_to_upstream():
                    async for msg in ws:
                        await upstream.send(msg)

                async def upstream_to_client():
                    async for msg in upstream:
                        await ws.send(msg)

                await asyncio.gather(
                    client_to_upstream(),
                    upstream_to_client(),
                )
        except websockets.exceptions.ConnectionClosed:
            pass
        except Exception as e:
            log.debug("WS proxy error: %s", e, extra={"component": "web"})

    # -- process_request: HTTP responses for non-WS requests --
    async def process_request(connection, request):
        """Handle HTTP requests; return None to allow WS upgrade on /ws."""
        path = request.path
        parsed = urlparse(path)
        clean = parsed.path.rstrip("/") or "/"

        # Allow WebSocket upgrade on /ws
        if clean == "/ws":
            return None

        query = parse_qs(parsed.query)
        status, headers, body = handle_http(clean, query)
        return websockets.http11.Response(
            status, "", websockets.datastructures.Headers(headers), body,
        )

    log.info("Transit Tracker web server at http://%s:%d", host, port, extra={"component": "web"})
    log.info("  /ws         — WebSocket relay (for HTTPS clients)", extra={"component": "web"})
    log.info("  /dashboard  — observability dashboard", extra={"component": "web"})
    log.info("  /simulator  — LED matrix simulator", extra={"component": "web"})
    log.info("  /spec       — API documentation page", extra={"component": "web"})

    async with websockets.serve(
        ws_proxy_handler, host, port,
        process_request=process_request,
    ):
        await asyncio.Future()  # Run forever



def generate_monitor_html() -> str:
    """Generate the network monitor page."""
    return r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Transit Tracker — Network Monitor</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect width='10' height='3' rx='1.5' fill='%23e8a830'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-0: #06080e;
  --bg-1: #0c0f1a;
  --bg-2: #141828;
  --bg-3: #1c2038;
  --border: #1a1e35;
  --border-hover: #282e4a;
  --text-0: #eae8e4;
  --text-1: #9498b0;
  --text-2: #5c6080;
  --text-3: #353850;
  --amber: #e8a830;
  --amber-bg: rgba(232,168,48,0.08);
  --cyan: #3d80d0;
  --cyan-bg: rgba(61,128,208,0.08);
  --green: #40b868;
  --green-bg: rgba(64,184,104,0.08);
  --red: #d84050;
  --red-bg: rgba(216,64,80,0.08);
  --purple: #9060c0;
  --purple-bg: rgba(144,96,192,0.08);
}
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Outfit', system-ui, -apple-system, sans-serif;
  background: var(--bg-0);
  color: var(--text-0);
  line-height: 1.5;
  min-height: 100vh;
}
body::after {
  content: '';
  position: fixed;
  inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E");
  opacity: 0.018;
  pointer-events: none;
  z-index: 9999;
}
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* ── Nav ── */
.nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 28px; height: 50px;
  background: var(--bg-1); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 100;
}
.nav-left { display: flex; align-items: center; gap: 24px; }
.nav-brand {
  font-weight: 700; font-size: 12px; letter-spacing: 2.5px;
  text-transform: uppercase; color: var(--amber);
  display: flex; align-items: center; gap: 10px;
  white-space: nowrap;
}
.nav-brand svg { flex-shrink: 0; }
.nav-links { display: flex; gap: 2px; }
.nav-link {
  font-size: 12.5px; font-weight: 500; color: var(--text-2);
  text-decoration: none; padding: 5px 12px; border-radius: 5px;
  transition: all 0.2s;
}
.nav-link:hover { color: var(--text-0); background: var(--bg-2); }
.nav-link.active { color: var(--amber); background: var(--amber-bg); }
.nav-right { display: flex; align-items: center; gap: 14px; }
.live-badge {
  display: flex; align-items: center; gap: 6px;
  font-size: 11.5px; font-weight: 500; color: var(--green);
}
.live-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 8px rgba(64,184,104,0.5);
  animation: pulse 2.5s ease-in-out infinite;
}
.live-dot.off { background: var(--red); box-shadow: 0 0 8px rgba(216,64,80,0.4); animation: none; }
@keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.35; transform: scale(0.8); } }

/* ── Layout ── */
.main {
  display: grid; grid-template-columns: 1.15fr 1fr;
  gap: 0; height: calc(100vh - 50px);
}
.col-left { border-right: 1px solid var(--border); display: flex; flex-direction: column; }
.col-right { display: flex; flex-direction: column; overflow: hidden; }

.section-hdr {
  padding: 9px 18px; font-size: 10px; text-transform: uppercase;
  letter-spacing: 1px; color: var(--text-2); font-weight: 600;
  border-bottom: 1px solid var(--border); background: var(--bg-1);
  display: flex; align-items: center; gap: 8px;
}
.section-hdr .dot { width: 5px; height: 5px; border-radius: 50%; }
.section-hdr .meta {
  margin-left: auto; font-size: 10px; text-transform: none;
  letter-spacing: 0; color: var(--text-3);
  font-family: 'IBM Plex Mono', monospace;
}

/* ── Topology ── */
.topo-wrap { flex: 1; position: relative; overflow: hidden; }
svg.topo { width: 100%; height: 100%; }
svg.topo text { font-family: 'IBM Plex Mono', monospace; }

/* ── Trip table ── */
.trip-section { border-top: 1px solid var(--border); }
.trip-table {
  width: 100%; border-collapse: collapse;
  font-size: 11.5px; font-family: 'IBM Plex Mono', monospace;
}
.trip-table th {
  text-align: left; padding: 5px 14px; color: var(--text-3);
  font-weight: 500; font-size: 9px; text-transform: uppercase;
  letter-spacing: 0.8px; border-bottom: 1px solid var(--border);
  font-family: 'Outfit', sans-serif;
}
.trip-table td { padding: 4px 14px; border-bottom: 1px solid rgba(20,24,40,0.5); }
.trip-table tr:hover td { background: var(--bg-2); }
.rt-dot { font-size: 12px; }
.rt-dot.live { color: var(--green); }
.rt-dot.sched { color: var(--text-3); }
.route-badge {
  display: inline-block; padding: 1px 7px; border-radius: 3px;
  font-weight: 600; font-size: 10.5px;
}

/* ── Feed ── */
.feed-list {
  flex: 1; overflow-y: auto;
  font-size: 11.5px; font-family: 'IBM Plex Mono', monospace;
}
.feed-entry {
  display: grid; grid-template-columns: 66px 1fr; gap: 0;
  padding: 3px 16px; border-bottom: 1px solid rgba(20,24,40,0.5);
  transition: background 0.1s;
}
.feed-entry:hover { background: var(--bg-2); }
.feed-ts { color: var(--text-3); white-space: nowrap; font-size: 10.5px; }
.feed-body { display: flex; flex-direction: column; gap: 1px; }
.feed-dir { font-weight: 600; font-size: 11.5px; }
.feed-detail { color: var(--text-1); font-size: 11.5px; }
.feed-json {
  color: var(--text-3); font-size: 10px; max-height: 48px; overflow: hidden;
  text-overflow: ellipsis; white-space: pre-wrap; word-break: break-all;
  margin-top: 2px; padding: 3px 6px; background: var(--bg-0); border-radius: 3px;
}
.dir-send { color: var(--cyan); }
.dir-recv { color: var(--green); }
.dir-err { color: var(--red); }
.dir-throttle { color: var(--amber); }
.dir-connect { color: var(--cyan); }
.dir-heartbeat { color: var(--text-3); }
.toggle-json { font-size: 10px; cursor: pointer; color: var(--purple); padding: 0 4px; }
.toggle-json:hover { text-decoration: underline; }

/* ── Sim checkbox ── */
.sim-label {
  display: flex; align-items: center; gap: 5px;
  cursor: pointer; font-size: 11.5px; color: var(--text-2);
  font-weight: 500;
}
.sim-label input { accent-color: var(--amber); }

@media (max-width: 800px) {
  .main { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
  .col-left { border-right: none; border-bottom: 1px solid var(--border); max-height: 50vh; }
  .nav-links { display: none; }
}
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-left">
    <div class="nav-brand">
      <svg width="18" height="13" viewBox="0 0 18 13" fill="none">
        <rect width="18" height="2.4" rx="1.2" fill="currentColor"/>
        <rect y="5" width="12" height="2.4" rx="1.2" fill="currentColor" opacity="0.55"/>
        <rect y="10" width="7" height="2.4" rx="1.2" fill="currentColor" opacity="0.25"/>
      </svg>
      Transit Tracker
    </div>
    <div class="nav-links">
      <a href="/dashboard" class="nav-link">Dashboard</a>
      <a href="/monitor" class="nav-link active">Monitor</a>
      <a href="/simulator" class="nav-link">Simulator</a>
      <a href="/spec" class="nav-link">API</a>
    </div>
  </div>
  <div class="nav-right">
    <label class="sim-label">
      <input type="checkbox" id="sim-toggle"> LED Simulator
    </label>
    <div class="live-badge">
      <span class="live-dot" id="live-dot"></span>
      <span id="conn-label">Connecting</span>
    </div>
  </div>
</nav>

<div class="main">
  <div class="col-left">
    <div class="section-hdr">
      <span class="dot" style="background:var(--cyan)"></span> Network Topology
    </div>
    <div class="topo-wrap">
      <svg class="topo" id="topo-svg" viewBox="0 0 600 500" preserveAspectRatio="xMidYMid meet"></svg>
      <iframe id="sim-iframe" src="about:blank" style="display:none;position:absolute;border:none;border-radius:4px;background:#000;z-index:10"></iframe>
    </div>
    <div class="trip-section">
      <div class="section-hdr">
        <span class="dot" style="background:var(--green)"></span> Last Schedule Push
        <span class="meta" id="trip-age"></span>
      </div>
      <div style="max-height:200px;overflow-y:auto">
        <table class="trip-table">
          <thead><tr><th>Route</th><th>Headsign</th><th style="text-align:right">ETA</th><th>RT</th><th>Stop</th></tr></thead>
          <tbody id="trip-body"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="col-right">
    <div class="section-hdr">
      <span class="dot" style="background:var(--amber)"></span> Message Flow
      <span class="meta" id="msg-count">0 events</span>
    </div>
    <div class="feed-list" id="feed-list"></div>
  </div>
</div>

<script>
(function() {
'use strict';

/* ── Simulator toggle ── */
var simToggle = document.getElementById('sim-toggle');
var simActive = false;
var simLoaded = false;
simToggle.addEventListener('change', function() {
  simActive = this.checked;
  if (!simActive) {
    /* Destroy iframe to close WebSocket connection */
    var iframe = document.getElementById('sim-iframe');
    iframe.src = 'about:blank';
    simLoaded = false;
  }
  renderTopo();
});

/* ── State ── */
var state = {}, prevState = {}, events = [];
var showJson = {};
var MAX_EVENTS = 200;

/* ── Helpers ── */
function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function ago(ts) {
  if (!ts) return 'never';
  var d = (Date.now() / 1000) - ts;
  if (d < 2) return 'just now';
  if (d < 60) return Math.floor(d) + 's ago';
  if (d < 3600) return Math.floor(d / 60) + 'm ago';
  return (d / 3600).toFixed(1) + 'h ago';
}
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function mins(at) {
  var d = (at - Date.now() / 1000) / 60;
  if (d <= 0) return 'Now';
  return Math.ceil(d) + 'm';
}

/* ── Events ── */
function addEvent(kind, dir, detail, jsonPayload) {
  events.push({ ts: Date.now() / 1000, kind: kind, dir: dir, detail: detail, json: jsonPayload || null });
  if (events.length > MAX_EVENTS) events = events.slice(-MAX_EVENTS);
}

function detectChanges(cur, prev) {
  var lu = cur.last_update || 0, plu = prev.last_update || 0;
  if (lu && lu !== plu) {
    var lm = cur.last_message || {};
    var trips = ((lm.data || {}).trips) || [];
    addEvent('send', 'server \u2192 clients', trips.length + ' trips pushed',
      JSON.stringify(lm, null, 2));
  }
  var cc = cur.client_count || 0, pc = prev.client_count || 0;
  if (cc > pc) addEvent('connect', '+' + (cc - pc) + ' client(s)', 'connected (total ' + cc + ')',
    JSON.stringify(cur.clients || [], null, 2));
  if (cc < pc) addEvent('err', '-' + (pc - cc) + ' client(s)', 'disconnected (total ' + cc + ')');
  var rl = cur.is_rate_limited, prl = prev.is_rate_limited;
  if (rl && !prl) addEvent('throttle', 'OBA \u2192 server', '429 rate limited');
  if (prl && !rl) addEvent('recv', 'OBA \u2192 server', 'Rate limit cleared');
  var ac = cur.api_calls_total || 0, pac = prev.api_calls_total || 0;
  if (ac > pac) addEvent('recv', 'server \u2192 OBA', (ac - pac) + ' API call(s) (total ' + ac + ')');
  var hb = cur.heartbeat || 0, phb = prev.heartbeat || 0;
  if (hb && hb !== phb) addEvent('heartbeat', 'server \u2192 clients', 'heartbeat');
}

/* ── SVG Topology ── */
function renderTopo() {
  var svg = document.getElementById('topo-svg');
  var clients = state.clients || [];
  var cc = state.client_count || 0;
  var running = state.status === 'active';
  var rl = state.is_rate_limited;
  var apiCalls = state.api_calls_total || 0;
  var throttle = state.throttle_total || 0;
  var refresh = state.refresh_interval || 30;
  var msgs = state.messages_processed || 0;
  var upH = state.uptime_hours || 0;
  var upStr = upH >= 1 ? upH.toFixed(1) + 'h' : Math.round(upH * 60) + 'm';
  var rows = Math.max(cc, 1);
  var simH = simActive ? 140 : 0;
  var svgH = 290 + rows * 56 + simH;
  svg.setAttribute('viewBox', '0 0 600 ' + svgH);

  var h = '';

  /* Defs */
  h += '<defs>';
  h += '<marker id="a" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#3d80d0"/></marker>';
  h += '<marker id="ag" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#40b868"/></marker>';
  h += '<filter id="glow"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>';
  h += '</defs>';

  /* OBA API node */
  var oy = 18;
  h += '<rect x="185" y="' + oy + '" width="230" height="72" rx="8" fill="#0c0f1a" stroke="' + (rl ? '#e8a830' : '#1a1e35') + '" stroke-width="' + (rl ? 2 : 1) + '"/>';
  h += '<text x="300" y="' + (oy + 21) + '" text-anchor="middle" fill="#e8a830" font-size="11.5" font-weight="700">OneBusAway API</text>';
  h += '<text x="300" y="' + (oy + 40) + '" text-anchor="middle" fill="' + (rl ? '#d84050' : '#40b868') + '" font-size="10.5" font-weight="600">' + (rl ? 'THROTTLED' : 'HEALTHY') + '</text>';
  h += '<text x="300" y="' + (oy + 57) + '" text-anchor="middle" fill="#353850" font-size="9.5">Calls: ' + apiCalls + '  \u00b7  429s: ' + throttle + '</text>';

  /* Wire: OBA -> Server */
  var wy1 = oy + 72, wy2 = oy + 128;
  if (rl) {
    h += '<line x1="300" y1="' + wy1 + '" x2="300" y2="' + wy2 + '" stroke="#d84050" stroke-width="1.5" stroke-dasharray="5,4"/>';
    h += '<text x="316" y="' + (wy1 + 26) + '" fill="#d84050" font-size="9.5" font-weight="600">429 BLOCKED</text>';
  } else {
    h += '<line x1="300" y1="' + wy1 + '" x2="300" y2="' + wy2 + '" stroke="#3d80d0" stroke-width="1.5" stroke-dasharray="5,4" marker-end="url(#a)">';
    h += '<animate attributeName="stroke-dashoffset" from="18" to="0" dur="0.8s" repeatCount="indefinite"/>';
    h += '</line>';
    h += '<text x="316" y="' + (wy1 + 26) + '" fill="#353850" font-size="9.5">arrivals / ' + refresh + 's</text>';
  }

  /* Server node */
  var sy = wy2;
  h += '<rect x="165" y="' + sy + '" width="270" height="90" rx="8" fill="#0c0f1a" stroke="' + (running ? '#3d80d0' : '#d84050') + '" stroke-width="' + (running ? 1.5 : 2) + '"/>';
  if (running) {
    h += '<rect x="165" y="' + sy + '" width="270" height="90" rx="8" fill="none" stroke="#3d80d0" stroke-width="1" opacity="0.12" filter="url(#glow)"/>';
  }
  h += '<circle cx="184" cy="' + (sy + 18) + '" r="3.5" fill="' + (running ? '#40b868' : '#d84050') + '"/>';
  h += '<text x="193" y="' + (sy + 22) + '" fill="' + (running ? '#40b868' : '#d84050') + '" font-size="11" font-weight="700">' + (running ? 'RUNNING' : 'STOPPED') + '</text>';
  h += '<text x="182" y="' + (sy + 40) + '" fill="#3d80d0" font-size="10.5">Transit Proxy :8000</text>';
  h += '<text x="182" y="' + (sy + 57) + '" fill="#353850" font-size="9.5">Up: ' + esc(upStr) + '  \u00b7  Msgs: ' + msgs + '</text>';
  h += '<text x="182" y="' + (sy + 72) + '" fill="#353850" font-size="9.5">Refresh: ' + refresh + 's  \u00b7  Clients: ' + cc + '</text>';

  /* Clients — bus topology: vertical trunk + horizontal branches */
  var cStartY = sy + 90 + 26;
  var trunkX = 155;
  var clientBoxX = 185;
  var clientBoxW = 240;
  var clientBoxH = 40;
  var clientSpacing = 52;

  /* Total nodes = real clients + simulator (if active) */
  var totalNodes = clients.length + (simActive ? 1 : 0);

  if (totalNodes === 0) {
    h += '<line x1="300" y1="' + (sy + 90) + '" x2="300" y2="' + cStartY + '" stroke="#1a1e35" stroke-width="1" stroke-dasharray="3,4"/>';
    h += '<text x="300" y="' + (cStartY + 16) + '" text-anchor="middle" fill="#353850" font-size="10.5" font-style="italic">No clients connected</text>';
  } else {
    /* Calculate last node Y for trunk length */
    var simNodeIdx = clients.length;
    var simBoxH = 110;
    var lastNodeBottomY;
    if (simActive) {
      var simCy = cStartY + simNodeIdx * clientSpacing;
      lastNodeBottomY = simCy + simBoxH / 2;
    } else {
      lastNodeBottomY = cStartY + (clients.length - 1) * clientSpacing + clientBoxH / 2;
    }

    /* Connector: server bottom center to trunk top */
    h += '<line x1="300" y1="' + (sy + 90) + '" x2="' + trunkX + '" y2="' + (sy + 90) + '" stroke="#40b868" stroke-width="1.5" stroke-dasharray="5,4"/>';

    /* Vertical trunk from server level down to last node midpoint */
    h += '<line x1="' + trunkX + '" y1="' + (sy + 90) + '" x2="' + trunkX + '" y2="' + lastNodeBottomY + '" stroke="#40b868" stroke-width="1.5" stroke-dasharray="5,4">';
    h += '<animate attributeName="stroke-dashoffset" from="0" to="-18" dur="1.2s" repeatCount="indefinite"/>';
    h += '</line>';

    /* Real client nodes */
    for (var i = 0; i < clients.length; i++) {
      var c = clients[i];
      var cy = cStartY + i * clientSpacing;
      var branchY = cy + clientBoxH / 2;
      var name = c.name || 'Unknown';
      var addr = (c.address || '?').split(':')[0];
      var subs = c.subscriptions || 0;
      var isLocal = addr === '127.0.0.1' || addr === 'localhost';
      var icon = isLocal ? '\uD83D\uDDA5\uFE0F' : '\uD83D\uDCFA';

      /* Horizontal branch: trunk to client box */
      h += '<line x1="' + trunkX + '" y1="' + branchY + '" x2="' + clientBoxX + '" y2="' + branchY + '" stroke="#40b868" stroke-width="1.5" stroke-dasharray="5,4" marker-end="url(#ag)">';
      h += '<animate attributeName="stroke-dashoffset" from="0" to="-18" dur="1.2s" repeatCount="indefinite"/>';
      h += '</line>';

      /* Client box */
      h += '<rect x="' + clientBoxX + '" y="' + cy + '" width="' + clientBoxW + '" height="' + clientBoxH + '" rx="6" fill="#0c0f1a" stroke="#1a1e35" stroke-width="1"/>';
      h += '<text x="' + (clientBoxX + 17) + '" y="' + (cy + 16) + '" fill="#eae8e4" font-size="13">' + icon + '</text>';
      h += '<text x="' + (clientBoxX + 39) + '" y="' + (cy + 16) + '" fill="#eae8e4" font-size="10.5" font-weight="600">' + esc(name) + '</text>';
      h += '<text x="' + (clientBoxX + 39) + '" y="' + (cy + 30) + '" fill="#353850" font-size="9.5">' + esc(addr) + ' \u00b7 ' + subs + ' subs</text>';
    }

    /* Simulator node — embedded as foreignObject on the bus */
    if (simActive) {
      var simCy = cStartY + simNodeIdx * clientSpacing;
      var simBranchY = simCy + simBoxH / 2;
      var simBoxW = 400;
      var simBoxX = clientBoxX;

      /* Horizontal branch: trunk to simulator */
      h += '<line x1="' + trunkX + '" y1="' + simBranchY + '" x2="' + simBoxX + '" y2="' + simBranchY + '" stroke="#e8a830" stroke-width="1.5" stroke-dasharray="5,4" marker-end="url(#ag)">';
      h += '<animate attributeName="stroke-dashoffset" from="0" to="-18" dur="1.2s" repeatCount="indefinite"/>';
      h += '</line>';

      /* Simulator container — tight border around canvas */
      h += '<rect x="' + simBoxX + '" y="' + simCy + '" width="' + simBoxW + '" height="' + simBoxH + '" rx="4" fill="#000" stroke="#e8a830" stroke-width="1.5" id="sim-placeholder"/>';
      h += '<rect x="' + simBoxX + '" y="' + simCy + '" width="' + simBoxW + '" height="' + simBoxH + '" rx="4" fill="none" stroke="#e8a830" stroke-width="1" opacity="0.1" filter="url(#glow)"/>';
    }
  }

  svg.innerHTML = h;

  /* Position the external iframe over the SVG placeholder */
  var simIframe = document.getElementById('sim-iframe');
  var placeholder = document.getElementById('sim-placeholder');
  if (simActive && placeholder) {
    /* Load iframe once */
    if (!simLoaded) {
      simLoaded = true;
      simIframe.src = '/simulator?embed=1';
    }
    /* Map SVG coords to screen coords */
    var svgEl = document.getElementById('topo-svg');
    var svgRect = svgEl.getBoundingClientRect();
    var viewBox = svgEl.viewBox.baseVal;
    var scaleX = svgRect.width / viewBox.width;
    var scaleY = svgRect.height / viewBox.height;
    var pBox = placeholder.getBBox();
    simIframe.style.display = 'block';
    simIframe.style.left = (pBox.x * scaleX) + 'px';
    simIframe.style.top = (pBox.y * scaleY) + 'px';
    simIframe.style.width = (pBox.width * scaleX) + 'px';
    simIframe.style.height = (pBox.height * scaleY) + 'px';
  } else {
    simIframe.style.display = 'none';
  }
}

/* ── Trip table ── */
function renderTrips() {
  var lm = state.last_message || {};
  var trips = ((lm.data || {}).trips) || [];
  var body = document.getElementById('trip-body');
  var ageEl = document.getElementById('trip-age');

  if (!trips.length) {
    body.innerHTML = '<tr><td colspan="5" style="color:var(--text-3);font-style:italic;padding:12px">Waiting for data\u2026</td></tr>';
    ageEl.textContent = '';
    return;
  }
  ageEl.textContent = ago(state.last_update);

  body.innerHTML = trips.slice(0, 10).map(function(t) {
    var rt = t.isRealtime;
    var at = t.arrivalTime > 1e12 ? t.arrivalTime / 1000 : t.arrivalTime;
    var color = t.routeColor ? '#' + t.routeColor : 'var(--cyan)';
    return '<tr>' +
      '<td><span class="route-badge" style="background:' + color + '15;color:' + color + '">' + esc(t.routeName || '?') + '</span></td>' +
      '<td>' + esc(t.headsign || '') + '</td>' +
      '<td style="text-align:right;font-weight:600">' + mins(at) + '</td>' +
      '<td><span class="rt-dot ' + (rt ? 'live' : 'sched') + '">' + (rt ? '\u25C9' : '\u25CB') + '</span></td>' +
      '<td style="color:var(--text-3)">' + esc(t.stopId || '') + '</td></tr>';
  }).join('');
}

/* ── Message feed ── */
function renderFeed() {
  var list = document.getElementById('feed-list');
  document.getElementById('msg-count').textContent = events.length + ' events';

  var atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  var vis = events.slice(-100);

  list.innerHTML = vis.map(function(ev, i) {
    var idx = events.length - vis.length + i;
    var ts = fmtTime(ev.ts);
    var dc = 'dir-send';
    if (ev.kind === 'recv') dc = 'dir-recv';
    else if (ev.kind === 'err') dc = 'dir-err';
    else if (ev.kind === 'throttle') dc = 'dir-throttle';
    else if (ev.kind === 'connect') dc = 'dir-connect';
    else if (ev.kind === 'heartbeat') dc = 'dir-heartbeat';

    var jh = '';
    if (ev.json) {
      var isVisible = showJson[idx];
      jh = '<span class="toggle-json" onclick="window._toggleJson(' + idx + ')">' + (isVisible ? '[\u2212]' : '[json]') + '</span>';
      if (isVisible) {
        jh += '<div class="feed-json">' + esc(ev.json.substring(0, 800)) + '</div>';
      }
    }

    return '<div class="feed-entry">' +
      '<span class="feed-ts">' + ts + '</span>' +
      '<div class="feed-body">' +
        '<div><span class="feed-dir ' + dc + '">' + esc(ev.dir) + '</span> ' +
        '<span class="feed-detail">' + esc(ev.detail) + '</span>' + jh + '</div>' +
      '</div></div>';
  }).join('');

  if (atBottom) list.scrollTop = list.scrollHeight;
}

window._toggleJson = function(idx) { showJson[idx] = !showJson[idx]; renderFeed(); };

/* ── Polling ── */
var lastLogTs = 0;

function poll() {
  fetch('/api/status?full=1').then(function(r) { return r.json(); }).then(function(cur) {
    if (cur.status === 'unavailable' || cur.status === 'error') {
      document.getElementById('live-dot').className = 'live-dot off';
      document.getElementById('conn-label').textContent = 'Unavailable';
      return;
    }
    document.getElementById('live-dot').className = 'live-dot';
    document.getElementById('conn-label').textContent = 'Live';

    detectChanges(cur, prevState);
    prevState = Object.assign({}, state);
    state = cur;

    renderTopo();
    renderTrips();
    renderFeed();
  }).catch(function() {
    document.getElementById('live-dot').className = 'live-dot off';
    document.getElementById('conn-label').textContent = 'Error';
  });
}

function pollLogs() {
  fetch('/api/logs?since=' + lastLogTs + '&limit=50').then(function(r) { return r.json(); }).then(function(data) {
    if (data.logs && data.logs.length) {
      for (var j = 0; j < data.logs.length; j++) {
        var entry = data.logs[j];
        var comp = entry.component || entry.logger || '';
        var msg = entry.msg || '';
        var level = entry.level || 'INFO';

        if (msg.indexOf('429') >= 0 || msg.indexOf('rate limit') >= 0) {
          addEvent('throttle', 'OBA \u2192 server', msg);
        } else if (msg.indexOf('connected') >= 0 && comp.indexOf('server') >= 0) {
          addEvent('connect', 'client \u2192 server', msg);
        } else if (msg.indexOf('disconnect') >= 0 && comp.indexOf('server') >= 0) {
          addEvent('err', 'server \u2715 client', msg);
        } else if (msg.indexOf('subscribe') >= 0) {
          addEvent('recv', 'client \u2192 server', msg,
            entry.pairs ? '{"pairs":' + entry.pairs + '}' : null);
        } else if (level === 'ERROR') {
          addEvent('err', comp, msg);
        }
      }
      lastLogTs = data.logs[data.logs.length - 1].ts + 0.001;
      renderFeed();
    }
  }).catch(function() {});
}

/* ── Boot ── */
poll();
pollLogs();
setInterval(poll, 2000);
setInterval(pollLogs, 3000);

})();
</script>
</body>
</html>
"""


def generate_dashboard_html() -> str:
    """Generate the observability dashboard."""
    return r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Transit Tracker — Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect width='10' height='3' rx='1.5' fill='%23e8a830'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-0: #06080e;
  --bg-1: #0c0f1a;
  --bg-2: #141828;
  --bg-3: #1c2038;
  --border: #1a1e35;
  --border-hover: #282e4a;
  --text-0: #eae8e4;
  --text-1: #9498b0;
  --text-2: #5c6080;
  --text-3: #353850;
  --amber: #e8a830;
  --amber-bg: rgba(232,168,48,0.08);
  --cyan: #3d80d0;
  --cyan-bg: rgba(61,128,208,0.08);
  --green: #40b868;
  --green-bg: rgba(64,184,104,0.08);
  --red: #d84050;
  --red-bg: rgba(216,64,80,0.08);
  --purple: #9060c0;
  --purple-bg: rgba(144,96,192,0.08);
}
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Outfit', system-ui, -apple-system, sans-serif;
  background: var(--bg-0);
  color: var(--text-0);
  line-height: 1.5;
  min-height: 100vh;
}
body::after {
  content: '';
  position: fixed;
  inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E");
  opacity: 0.018;
  pointer-events: none;
  z-index: 9999;
}
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* ── Nav ── */
.nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 28px; height: 50px;
  background: var(--bg-1); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 100;
}
.nav-left { display: flex; align-items: center; gap: 24px; }
.nav-brand {
  font-weight: 700; font-size: 12px; letter-spacing: 2.5px;
  text-transform: uppercase; color: var(--amber);
  display: flex; align-items: center; gap: 10px;
  white-space: nowrap;
}
.nav-brand svg { flex-shrink: 0; }
.nav-links { display: flex; gap: 2px; }
.nav-link {
  font-size: 12.5px; font-weight: 500; color: var(--text-2);
  text-decoration: none; padding: 5px 12px; border-radius: 5px;
  transition: all 0.2s;
}
.nav-link:hover { color: var(--text-0); background: var(--bg-2); }
.nav-link.active { color: var(--amber); background: var(--amber-bg); }
.nav-right { display: flex; align-items: center; gap: 16px; }
.live-badge {
  display: flex; align-items: center; gap: 6px;
  font-size: 11.5px; font-weight: 500; color: var(--green);
}
.live-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 8px rgba(64,184,104,0.5);
  animation: pulse 2.5s ease-in-out infinite;
}
.live-dot.off { background: var(--red); box-shadow: 0 0 8px rgba(216,64,80,0.4); animation: none; }
@keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.35; transform: scale(0.8); } }
.time-range { display: flex; border: 1px solid var(--border); border-radius: 5px; overflow: hidden; }
.time-range button {
  background: none; border: none; color: var(--text-2);
  padding: 3px 10px; font-size: 11px; font-weight: 600;
  cursor: pointer; transition: all 0.15s;
  font-family: 'Outfit', sans-serif; letter-spacing: 0.3px;
}
.time-range button:hover:not(.active) { color: var(--text-0); }
.time-range button.active { background: var(--amber); color: #080a10; }
.time-range button + button { border-left: 1px solid var(--border); }

/* ── Info strip ── */
.info-strip {
  display: flex; align-items: center; gap: 16px;
  padding: 8px 28px;
  border-bottom: 1px solid var(--border);
  font-size: 11.5px; color: var(--text-2);
  background: var(--bg-1);
}
.info-chip {
  display: flex; align-items: center; gap: 6px;
  padding: 2px 10px; border-radius: 4px;
  background: var(--bg-2); border: 1px solid var(--border);
}
.info-chip .label { color: var(--text-2); font-weight: 400; }
.info-chip .val {
  color: var(--text-0); font-weight: 600;
  font-family: 'IBM Plex Mono', monospace; font-size: 11px;
}
.info-chip .val.amber { color: var(--amber); }
.info-chip .val.green { color: var(--green); }

/* ── Container ── */
.container { padding: 18px 28px; max-width: 1440px; margin: 0 auto; }

/* ── Stat grid ── */
.stat-grid {
  display: grid; gap: 10px; margin-bottom: 14px;
  grid-template-columns: repeat(6, 1fr);
}
.stat-card {
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 16px;
  position: relative; overflow: hidden;
  transition: border-color 0.2s, transform 0.15s;
}
.stat-card:hover { border-color: var(--border-hover); transform: translateY(-1px); }
.stat-card::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0;
  width: 3px; border-radius: 3px 0 0 3px;
}
.stat-card[data-accent="amber"]::before { background: var(--amber); }
.stat-card[data-accent="cyan"]::before { background: var(--cyan); }
.stat-card[data-accent="green"]::before { background: var(--green); }
.stat-card[data-accent="red"]::before { background: var(--red); }
.stat-card[data-accent="purple"]::before { background: var(--purple); }
.stat-label {
  font-size: 9.5px; text-transform: uppercase; letter-spacing: 1px;
  color: var(--text-2); font-weight: 600; margin-bottom: 6px;
}
.stat-value {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 24px; font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.stat-sub { font-size: 10.5px; color: var(--text-2); margin-top: 4px; }
.stat-value.amber { color: var(--amber); }
.stat-value.cyan { color: var(--cyan); }
.stat-value.green { color: var(--green); }
.stat-value.red { color: var(--red); }
.stat-value.purple { color: var(--purple); }

/* ── Charts ── */
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
.chart-card {
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 16px; overflow: hidden;
}
.chart-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px;
}
.chart-title {
  font-size: 11.5px; font-weight: 600; color: var(--text-1);
  display: flex; align-items: center; gap: 7px;
}
.chart-title .dot { width: 5px; height: 5px; border-radius: 50%; }
.chart-latest {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px; font-weight: 600;
}
canvas { width: 100% !important; height: 130px !important; display: block; }

/* ── Log panel ── */
.log-panel {
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: 8px; overflow: hidden;
}
.log-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px; border-bottom: 1px solid var(--border);
}
.log-title { font-size: 12px; font-weight: 600; color: var(--text-1); }
.log-count {
  font-family: 'IBM Plex Mono', monospace; font-size: 10px;
  color: var(--text-3); margin-left: 8px; font-weight: 400;
}
.log-filters { display: flex; gap: 3px; }
.log-filters button {
  background: var(--bg-2); border: 1px solid var(--border);
  color: var(--text-2); padding: 2px 9px; border-radius: 4px;
  font-size: 10.5px; font-weight: 500; cursor: pointer;
  font-family: 'Outfit', sans-serif; transition: all 0.15s;
}
.log-filters button.active { background: var(--amber); border-color: var(--amber); color: #080a10; }
.log-filters button:hover:not(.active) { border-color: var(--text-2); }
.log-list {
  max-height: 320px; overflow-y: auto;
  font-family: 'IBM Plex Mono', monospace; font-size: 11.5px; line-height: 1.7;
}
.log-entry {
  padding: 1px 16px; border-bottom: 1px solid rgba(20,24,40,0.6);
  display: flex; gap: 10px; transition: background 0.1s;
}
.log-entry:hover { background: var(--bg-2); }
.log-ts { color: var(--text-3); white-space: nowrap; min-width: 70px; }
.log-level { font-weight: 600; min-width: 40px; text-align: center; }
.log-level.DEBUG { color: var(--text-3); }
.log-level.INFO { color: var(--cyan); }
.log-level.WARNING { color: var(--amber); }
.log-level.ERROR { color: var(--red); }
.log-comp { color: var(--purple); min-width: 48px; opacity: 0.6; }
.log-msg { color: var(--text-1); flex: 1; word-break: break-all; }

/* ── Responsive ── */
@media (max-width: 1100px) {
  .stat-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 900px) {
  .chart-grid { grid-template-columns: 1fr; }
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .info-strip { flex-wrap: wrap; gap: 8px; }
}
@media (max-width: 500px) {
  .container { padding: 12px; }
  .nav { padding: 0 14px; }
  .stat-grid { grid-template-columns: 1fr; }
  .stat-value { font-size: 20px; }
  .nav-links { display: none; }
}

/* ── Animations ── */
@keyframes fadeSlideIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}
.stat-card, .chart-card, .log-panel { animation: fadeSlideIn 0.5s cubic-bezier(0.22,1,0.36,1) both; }
.stat-card:nth-child(1) { animation-delay: 0.04s; }
.stat-card:nth-child(2) { animation-delay: 0.08s; }
.stat-card:nth-child(3) { animation-delay: 0.12s; }
.stat-card:nth-child(4) { animation-delay: 0.16s; }
.stat-card:nth-child(5) { animation-delay: 0.20s; }
.stat-card:nth-child(6) { animation-delay: 0.24s; }
.chart-card:nth-child(1) { animation-delay: 0.30s; }
.chart-card:nth-child(2) { animation-delay: 0.34s; }
.chart-card:nth-child(3) { animation-delay: 0.38s; }
.chart-card:nth-child(4) { animation-delay: 0.42s; }
.log-panel { animation-delay: 0.48s; }
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-left">
    <div class="nav-brand">
      <svg width="18" height="13" viewBox="0 0 18 13" fill="none">
        <rect width="18" height="2.4" rx="1.2" fill="currentColor"/>
        <rect y="5" width="12" height="2.4" rx="1.2" fill="currentColor" opacity="0.55"/>
        <rect y="10" width="7" height="2.4" rx="1.2" fill="currentColor" opacity="0.25"/>
      </svg>
      Transit Tracker
    </div>
    <div class="nav-links">
      <a href="/dashboard" class="nav-link active">Dashboard</a>
      <a href="/monitor" class="nav-link">Monitor</a>
      <a href="/simulator" class="nav-link">Simulator</a>
      <a href="/spec" class="nav-link">API</a>
    </div>
  </div>
  <div class="nav-right">
    <div class="time-range">
      <button class="active" data-range="300">5m</button>
      <button data-range="900">15m</button>
      <button data-range="1800">30m</button>
      <button data-range="0">All</button>
    </div>
    <div class="live-badge">
      <span class="live-dot" id="live-dot"></span>
      <span id="live-label">Live</span>
    </div>
  </div>
</nav>

<div class="info-strip">
  <div class="info-chip">
    <span class="label">Profile</span>
    <span class="val amber" id="i-profile">&mdash;</span>
  </div>
  <div class="info-chip">
    <span class="label">API Key</span>
    <span class="val" id="i-apikey">&mdash;</span>
  </div>
  <div class="info-chip">
    <span class="label">Refresh</span>
    <span class="val" id="i-refresh">&mdash;</span>
  </div>
</div>

<div class="container">
  <div class="stat-grid">
    <div class="stat-card" data-accent="green">
      <div class="stat-label">Uptime</div>
      <div class="stat-value green" id="s-uptime">&mdash;</div>
      <div class="stat-sub" id="s-uptime-sub"></div>
    </div>
    <div class="stat-card" data-accent="cyan">
      <div class="stat-label">Active Clients</div>
      <div class="stat-value cyan" id="s-clients">0</div>
      <div class="stat-sub" id="s-clients-sub"></div>
    </div>
    <div class="stat-card" data-accent="purple">
      <div class="stat-label">Messages Sent</div>
      <div class="stat-value purple" id="s-msgs">0</div>
      <div class="stat-sub" id="s-msgs-sub"></div>
    </div>
    <div class="stat-card" data-accent="amber">
      <div class="stat-label">API Calls</div>
      <div class="stat-value amber" id="s-api">0</div>
      <div class="stat-sub" id="s-api-sub"></div>
    </div>
    <div class="stat-card" data-accent="amber">
      <div class="stat-label">429 Throttles</div>
      <div class="stat-value" id="s-throttle">0</div>
      <div class="stat-sub" id="s-throttle-sub"></div>
    </div>
    <div class="stat-card" data-accent="red">
      <div class="stat-label">API Errors</div>
      <div class="stat-value" id="s-errors">0</div>
      <div class="stat-sub" id="s-errors-sub"></div>
    </div>
  </div>

  <div class="chart-grid">
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title"><span class="dot" style="background:var(--cyan)"></span>API Latency</div>
        <span class="chart-latest" id="cl-latency" style="color:var(--cyan)">&mdash;</span>
      </div>
      <canvas id="chart-latency"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title"><span class="dot" style="background:var(--green)"></span>Active Clients</div>
        <span class="chart-latest" id="cl-clients" style="color:var(--green)">&mdash;</span>
      </div>
      <canvas id="chart-clients"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title"><span class="dot" style="background:var(--purple)"></span>Refresh Interval</div>
        <span class="chart-latest" id="cl-interval" style="color:var(--purple)">&mdash;</span>
      </div>
      <canvas id="chart-interval"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title"><span class="dot" style="background:var(--amber)"></span>Throttle Rate</div>
        <span class="chart-latest" id="cl-throttle" style="color:var(--amber)">&mdash;</span>
      </div>
      <canvas id="chart-throttle"></canvas>
    </div>
  </div>

  <div class="log-panel">
    <div class="log-header">
      <div class="log-title">Event Log <span class="log-count" id="log-count"></span></div>
      <div class="log-filters">
        <button class="active" data-level="all">All</button>
        <button data-level="ERROR">Error</button>
        <button data-level="WARNING">Warn</button>
        <button data-level="INFO">Info</button>
        <button data-level="DEBUG">Debug</button>
      </div>
    </div>
    <div class="log-list" id="log-list"></div>
  </div>
</div>

<script>
(function() {
'use strict';

/* ── Smooth Chart Renderer ── */
class Chart {
  constructor(canvas, color) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.color = color;
    this.data = [];
    this._resize();
    window.addEventListener('resize', () => this._resize());
  }
  _resize() {
    var r = this.canvas.parentElement.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    this.canvas.width = r.width * dpr;
    this.canvas.height = 130 * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = r.width;
    this.h = 130;
    this.draw();
  }
  setData(pts) { this.data = pts; this.draw(); }
  draw() {
    var ctx = this.ctx, w = this.w, h = this.h, d = this.data;
    ctx.clearRect(0, 0, w, h);
    if (d.length < 2) {
      ctx.fillStyle = '#353850';
      ctx.font = '500 11px Outfit, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Awaiting data\u2026', w / 2, h / 2);
      return;
    }
    /* Grid */
    ctx.strokeStyle = '#111428';
    ctx.lineWidth = 1;
    for (var g = 1; g < 4; g++) {
      var gy = (h / 4) * g;
      ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke();
    }
    var vals = d.map(function(p) { return p[1]; });
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    if (max === min) max = min + 1;
    var pad = 4, ih = h - pad * 2;
    var xStep = w / (d.length - 1);
    /* Build points */
    var pts = [];
    for (var i = 0; i < d.length; i++) {
      pts.push({ x: i * xStep, y: pad + ih - ((vals[i] - min) / (max - min)) * ih });
    }
    /* Area fill with bezier */
    ctx.beginPath();
    ctx.moveTo(0, h);
    ctx.lineTo(pts[0].x, pts[0].y);
    for (var i = 1; i < pts.length; i++) {
      var cpx = (pts[i - 1].x + pts[i].x) / 2;
      ctx.bezierCurveTo(cpx, pts[i - 1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    var grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, this.color + '25');
    grad.addColorStop(1, this.color + '02');
    ctx.fillStyle = grad;
    ctx.fill();
    /* Stroke with bezier */
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (var i = 1; i < pts.length; i++) {
      var cpx = (pts[i - 1].x + pts[i].x) / 2;
      ctx.bezierCurveTo(cpx, pts[i - 1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
    }
    ctx.strokeStyle = this.color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    /* End dot */
    var last = pts[pts.length - 1];
    ctx.beginPath();
    ctx.arc(last.x, last.y, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = this.color;
    ctx.fill();
    /* Y scale labels */
    ctx.fillStyle = '#2a2e48';
    ctx.font = '10px IBM Plex Mono, monospace';
    ctx.textAlign = 'left';
    ctx.fillText(max.toFixed(1), 3, pad + 10);
    ctx.fillText(min.toFixed(1), 3, h - 3);
  }
}

/* ── Init charts ── */
var charts = {
  latency: new Chart(document.getElementById('chart-latency'), '#3d80d0'),
  clients: new Chart(document.getElementById('chart-clients'), '#40b868'),
  interval: new Chart(document.getElementById('chart-interval'), '#9060c0'),
  throttle: new Chart(document.getElementById('chart-throttle'), '#e8a830')
};

var timeRange = 300;
var logFilter = 'all';
var lastLogTs = 0;
var allLogs = [];

/* ── Time range buttons ── */
document.querySelectorAll('.time-range button').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.time-range button').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    timeRange = parseInt(btn.dataset.range);
    fetchMetrics();
  });
});

/* ── Log filter buttons ── */
document.querySelectorAll('.log-filters button').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.log-filters button').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    logFilter = btn.dataset.level;
    renderLogs();
  });
});

/* ── Helpers ── */
function fmtUp(s) {
  if (s < 60) return Math.floor(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  var hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
  return hh + 'h ' + mm + 'm';
}
function fmtN(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return '' + n;
}
function fmtT(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

/* ── Fetch status for info bar ── */
function fetchStatus() {
  fetch('/api/status').then(function(r) { return r.json(); }).then(function(s) {
    if (s.status === 'unavailable' || s.status === 'error') return;
    var prof = (s.config_path || '').split('/').pop() || '\u2014';
    document.getElementById('i-profile').textContent = prof;
    var key = s.oba_api_key || 'TEST';
    var keyEl = document.getElementById('i-apikey');
    keyEl.textContent = key;
    keyEl.className = 'val ' + (key === 'TEST' ? '' : 'green');
    document.getElementById('i-refresh').textContent = (s.refresh_interval || 30) + 's';
  }).catch(function() {});
}

/* ── Fetch metrics ── */
function fetchMetrics() {
  var since = timeRange > 0 ? (Date.now() / 1000 - timeRange) : 0;
  fetch('/api/metrics?since=' + since).then(function(r) { return r.json(); }).then(function(m) {
    document.getElementById('live-dot').className = 'live-dot';
    document.getElementById('live-label').textContent = 'Live';

    document.getElementById('s-uptime').textContent = fmtUp(m.uptime_s);
    var t0 = new Date((m.ts - m.uptime_s) * 1000);
    document.getElementById('s-uptime-sub').textContent = 'since ' + t0.toLocaleTimeString();

    document.getElementById('s-clients').textContent = m.gauges.active_clients;
    document.getElementById('s-clients-sub').textContent = fmtN(m.counters.ws_connections) + ' total connections';

    document.getElementById('s-msgs').textContent = fmtN(m.counters.messages_sent);
    document.getElementById('s-msgs-sub').textContent = fmtN(m.counters.messages_received) + ' received';

    var ac = m.counters.api_calls;
    document.getElementById('s-api').textContent = fmtN(ac);
    var errR = ac > 0 ? (m.counters.api_errors / ac * 100).toFixed(1) : '0.0';
    document.getElementById('s-api-sub').textContent = errR + '% error rate';

    var thr = m.counters.throttle_events;
    var thrEl = document.getElementById('s-throttle');
    thrEl.textContent = fmtN(thr);
    thrEl.className = 'stat-value ' + (thr > 0 ? 'amber' : 'green');
    var thrR = ac > 0 ? (thr / ac * 100).toFixed(1) : '0.0';
    document.getElementById('s-throttle-sub').textContent = thrR + '% of calls';

    var errEl = document.getElementById('s-errors');
    errEl.textContent = fmtN(m.counters.api_errors);
    errEl.className = 'stat-value ' + (m.counters.api_errors > 0 ? 'red' : 'green');
    document.getElementById('s-errors-sub').textContent = fmtN(m.counters.api_errors) + ' total';

    charts.latency.setData(m.series.api_latency_ms);
    charts.clients.setData(m.series.active_clients);
    charts.interval.setData(m.series.refresh_interval_s);
    charts.throttle.setData(m.series.throttle_rate);

    function latest(arr) { return arr.length ? arr[arr.length - 1][1] : null; }
    var ll = latest(m.series.api_latency_ms);
    document.getElementById('cl-latency').textContent = ll !== null ? ll.toFixed(0) + ' ms' : '\u2014';
    var lc = latest(m.series.active_clients);
    document.getElementById('cl-clients').textContent = lc !== null ? Math.round(lc) + '' : '\u2014';
    var li = latest(m.series.refresh_interval_s);
    document.getElementById('cl-interval').textContent = li !== null ? li.toFixed(0) + 's' : '\u2014';
    var lt = latest(m.series.throttle_rate);
    document.getElementById('cl-throttle').textContent = lt !== null ? lt.toFixed(1) + '%' : '\u2014';
  }).catch(function() {
    document.getElementById('live-dot').className = 'live-dot off';
    document.getElementById('live-label').textContent = 'Offline';
  });
}

/* ── Fetch logs ── */
function fetchLogs() {
  fetch('/api/logs?since=' + lastLogTs + '&limit=200').then(function(r) { return r.json(); }).then(function(data) {
    if (data.logs && data.logs.length) {
      allLogs = allLogs.concat(data.logs);
      if (allLogs.length > 500) allLogs = allLogs.slice(-500);
      lastLogTs = data.logs[data.logs.length - 1].ts + 0.001;
      renderLogs();
    }
  }).catch(function() {});
}

function renderLogs() {
  var list = document.getElementById('log-list');
  var f = allLogs;
  if (logFilter !== 'all') f = allLogs.filter(function(e) { return e.level === logFilter; });
  var vis = f.slice(-200);
  document.getElementById('log-count').textContent = '(' + allLogs.length + ')';
  list.innerHTML = vis.map(function(e) {
    var lv = e.level || 'INFO';
    var comp = e.component || e.logger || '';
    return '<div class="log-entry">' +
      '<span class="log-ts">' + fmtT(e.ts) + '</span>' +
      '<span class="log-level ' + lv + '">' + lv + '</span>' +
      '<span class="log-comp">' + esc(comp) + '</span>' +
      '<span class="log-msg">' + esc(e.msg || '') + '</span></div>';
  }).join('');
  list.scrollTop = list.scrollHeight;
}

/* ── Boot ── */
fetchStatus();
fetchMetrics();
fetchLogs();
setInterval(fetchStatus, 5000);
setInterval(fetchMetrics, 2000);
setInterval(fetchLogs, 2000);

})();
</script>
</body>
</html>
"""


def generate_simulator_html() -> str:
    """Generate a self-contained browser-based LED matrix simulator.

    Connects to the WebSocket server client-side, renders a pixel-perfect
    HUB75 LED matrix on an HTML5 Canvas, with MicroFont glyph data, realtime
    icon animation, and headsign scrolling.
    """
    from .simulator import BaseSimulator, MicroFont

    # Export glyph data as JSON for the JS renderer
    glyphs_json = json.dumps({k: v for k, v in MicroFont.GLYPHS.items()})
    icon_json = json.dumps(MicroFont.REALTIME_ICON)

    # Build subscribe payload from current config so the web simulator
    # sends real subscription data instead of relying on server defaults.
    try:
        from .config import TransitConfig, get_last_config_path, load_service_settings

        svc = load_service_settings()
        config_path = (
            get_last_config_path()
            or os.environ.get("CONFIG_PATH")
            or ("/config/config.yaml" if os.path.exists("/config/config.yaml") else None)
            or "config.yaml"
        )
        config = TransitConfig.load(config_path, service_settings=svc)

        class _Stub(BaseSimulator):
            async def run(self):
                pass

        stub = _Stub(config, force_live=True)
        sub_payload = stub.build_subscribe_payload(
            client_name="WebSimulator",
            limit=10,
        )
        subscribe_json = json.dumps(sub_payload)
    except Exception:
        subscribe_json = json.dumps({
            "event": "schedule:subscribe",
            "client_name": "WebSimulator",
            "data": {"routeStopPairs": "", "limit": 10},
        })

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit Tracker — LED Simulator</title>
<style>
:root {{
  --bg: #0a0a0a;
  --bg-card: #141418;
  --border: #252530;
  --text: #dde1ed;
  --text2: #8891b0;
  --muted: #505872;
  --green: #00c853;
  --red: #ff1744;
  --purple: #7c4dff;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'SF Mono','Menlo','Consolas',monospace; background: var(--bg); color: var(--text); }}
.header {{
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 24px; border-bottom: 1px solid var(--border); background: var(--bg-card);
}}
.header h1 {{ font-size: 14px; font-weight: 600; }}
.header h1 span {{ color: var(--purple); }}
.controls {{
  display: flex; align-items: center; gap: 16px; font-size: 12px; color: var(--text2);
}}
.controls select, .controls input {{
  background: #1a1a24; border: 1px solid var(--border); color: var(--text);
  padding: 4px 8px; border-radius: 4px; font-family: inherit; font-size: 12px;
}}
.status-dot {{
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: var(--red); transition: background 0.3s;
}}
.status-dot.connected {{ background: var(--green); animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
.sim-container {{
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-height: calc(100vh - 49px); padding: 40px;
}}
canvas {{
  image-rendering: pixelated; border: 2px solid #333;
  border-radius: 4px; background: #000;
}}
.info {{
  margin-top: 16px; font-size: 11px; color: var(--muted); text-align: center;
}}
a {{ color: var(--purple); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
/* Embed mode: hide header, fill container */
body.embed {{ background: transparent; }}
body.embed .header {{ display: none; }}
body.embed .sim-container {{ min-height: auto; padding: 0; justify-content: flex-start; }}
body.embed .info {{ display: none; }}
body.embed canvas {{ border: none; border-radius: 0; }}
</style>
</head>
<body>
<script>if(location.search.indexOf('embed=1')>=0)document.body.classList.add('embed');</script>
<div class="header">
  <h1><span>Transit Tracker</span> &mdash; LED Simulator</h1>
  <div class="controls">
    <span><span class="status-dot" id="ws-dot"></span> <span id="ws-label">Disconnected</span></span>
    <label>Endpoint:
      <select id="endpoint-select">
        <option value="local">Local (via /ws proxy)</option>
        <option value="cloud">Cloud (tt.horner.tj)</option>
        <option value="custom">Custom...</option>
      </select>
    </label>
    <input type="text" id="custom-url" placeholder="ws://host:port" style="display:none;width:200px;">
    <a href="/monitor">Monitor &rarr;</a>
  </div>
</div>
<div class="sim-container">
  <canvas id="led-canvas"></canvas>
  <div class="info">
    <div id="trip-info">Waiting for data...</div>
    <div style="margin-top:8px;">Pixel scale: <input type="range" id="scale-range" min="2" max="12" value="6" style="vertical-align:middle;width:100px;">
    <span id="scale-label">6x</span></div>
  </div>
</div>

<script>
// ---- Config ----
const PANEL_W = 64, PANEL_H = 32, NUM_PANELS = 2;
const DISPLAY_W = PANEL_W * NUM_PANELS;
const DISPLAY_H = PANEL_H;
let PIXEL_SCALE = (location.search.indexOf('embed=1') >= 0) ? 3 : 6;
const PIXEL_GAP = 1;

// ---- Glyph data from Python MicroFont ----
const GLYPHS = {glyphs_json};
const REALTIME_ICON = {icon_json};

// ---- Subscribe payload from config ----
const SUBSCRIBE_PAYLOAD = {subscribe_json};

// ---- State ----
let ws = null;
let trips = [];
let startTime = Date.now();
let subscribePayload = null;

// ---- Canvas setup ----
const canvas = document.getElementById('led-canvas');
const ctx = canvas.getContext('2d');

function resizeCanvas() {{
  canvas.width = DISPLAY_W * PIXEL_SCALE;
  canvas.height = DISPLAY_H * PIXEL_SCALE;
}}
resizeCanvas();

// ---- Scale slider ----
const scaleRange = document.getElementById('scale-range');
const scaleLabel = document.getElementById('scale-label');
scaleRange.addEventListener('input', () => {{
  PIXEL_SCALE = parseInt(scaleRange.value);
  scaleLabel.textContent = PIXEL_SCALE + 'x';
  resizeCanvas();
}});

// ---- Font rendering ----
function getGlyphBitmap(ch) {{
  const key = ch.toUpperCase();
  const glyph = GLYPHS[key] || GLYPHS[ch] || GLYPHS['?'];
  if (!glyph) return Array.from({{length:7}}, () => []);
  const rows = [];
  for (let i = 0; i < 7; i++) {{
    const bits = glyph[i];
    const row = [];
    for (let b = 4; b >= 0; b--) {{
      row.push((bits >> b) & 1);
    }}
    row.push(0); // gap
    rows.push(row);
  }}
  return rows;
}}

function getTextBitmap(text) {{
  const rows = Array.from({{length:7}}, () => []);
  for (const ch of text) {{
    const glyph = getGlyphBitmap(ch);
    for (let i = 0; i < 7; i++) {{
      rows[i].push(...glyph[i]);
    }}
  }}
  return rows;
}}

function getIconFrame(elapsed) {{
  const cycleMs = Math.floor(elapsed * 1000) % 4000;
  let frame = 0;
  if (cycleMs >= 3000) {{
    frame = Math.min(Math.floor((cycleMs - 3000) / 200) + 1, 5);
  }}
  const rows = Array.from({{length:7}}, () => []);
  for (let r = 0; r < 6; r++) {{
    for (let c = 0; c < 6; c++) {{
      const seg = REALTIME_ICON[r][c];
      if (seg === 0) {{ rows[r].push(0); continue; }}
      let lit = false;
      if (seg === 1 && [1,2,3].includes(frame)) lit = true;
      else if (seg === 2 && [2,3,4].includes(frame)) lit = true;
      else if (seg === 3 && [3,4,5].includes(frame)) lit = true;
      rows[r].push(lit ? 2 : 1);
    }}
  }}
  rows[6] = [0,0,0,0,0,0];
  return rows;
}}

// ---- Color helpers ----
function parseColor(c) {{
  if (!c) return '#cccc00';
  if (c === 'hot_pink') return '#ff69b4';
  if (c === 'yellow') return '#cccc00';
  if (c === 'white') return '#ffffff';
  if (c === 'bright_blue') return '#5599ff';
  if (c === 'grey74') return '#bbbbbb';
  if (c.startsWith('#')) return c;
  return '#cccc00';
}}

function dimColor(c) {{
  // Return a very dim version for the "dim segment" of the icon
  return '#223366';
}}

// ---- Trip processing (mirrors BaseSimulator._process_trip) ----
function processDepartures(rawTrips) {{
  const now = Date.now();
  const deps = [];
  for (const trip of rawTrips) {{
    const tripId = trip.tripId;
    if (!tripId) continue;
    let arrVal = trip.arrivalTime || trip.predictedArrivalTime || 0;
    if (!arrVal) continue;
    const baseMs = arrVal > 1e12 ? arrVal : arrVal * 1000;
    const diffMin = Math.floor((baseMs - now) / 60000);
    if (diffMin < -1) continue;

    const routeName = trip.routeName || '?';
    const headsign = trip.headsign || 'Transit';
    const isLive = !!trip.isRealtime;
    const colorHex = trip.routeColor;
    let color = 'yellow';
    if (routeName.includes('14')) color = 'hot_pink';
    else if (colorHex) color = '#' + colorHex;

    deps.push({{
      trip_id: tripId,
      diff: Math.max(0, diffMin),
      route: routeName,
      headsign: headsign,
      color: color,
      live: isLive,
      stop_id: trip.stopId || '',
    }});
  }}
  deps.sort((a,b) => a.diff - b.diff);
  // Diversity cap: 1 per stop, then fill to 3
  const final = [];
  const seen = new Set();
  for (const d of deps) {{
    if (!seen.has(d.stop_id)) {{ final.push(d); seen.add(d.stop_id); }}
    if (final.length >= 3) break;
  }}
  if (final.length < 3) {{
    for (const d of deps) {{
      if (!final.includes(d)) final.push(d);
      if (final.length >= 3) break;
    }}
  }}
  final.sort((a,b) => a.diff - b.diff);
  return final;
}}

// ---- Render frame ----
function renderFrame() {{
  const elapsed = (Date.now() - startTime) / 1000;
  ctx.fillStyle = '#000000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const deps = processDepartures(trips);

  if (deps.length === 0) {{
    const msg = trips.length === 0 ? 'Connecting...' : 'No Live Buses';
    const bm = getTextBitmap(msg);
    for (let r = 0; r < 7; r++) {{
      for (let c = 0; c < bm[r].length; c++) {{
        if (bm[r][c]) {{
          ctx.fillStyle = '#00cccc';
          ctx.fillRect(c * PIXEL_SCALE, r * PIXEL_SCALE,
                       PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
        }}
      }}
    }}
    requestAnimationFrame(renderFrame);
    return;
  }}

  let rowY = 0;
  for (let di = 0; di < Math.min(deps.length, 3); di++) {{
    const dep = deps[di];
    const timeStr = dep.diff <= 0 ? 'Now' : dep.diff + 'm';
    const routeColor = parseColor(dep.color);
    const timeColor = dep.live ? '#5599ff' : '#bbbbbb';

    // Compute segment widths
    const routeBm = getTextBitmap(dep.route + '  ');
    const headsignBm = getTextBitmap(dep.headsign);
    const timeBm = getTextBitmap(' ' + timeStr);
    const iconW = dep.live ? 8 : 0;
    const fixedW = routeBm[0].length + timeBm[0].length + iconW;
    const headsignAreaW = Math.max(0, DISPLAY_W - fixedW);

    let x = 0;
    // Route
    for (let r = 0; r < 7; r++) {{
      for (let c = 0; c < routeBm[r].length; c++) {{
        if (routeBm[r][c]) {{
          ctx.fillStyle = routeColor;
          ctx.fillRect((x + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                       PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
        }}
      }}
    }}
    x += routeBm[0].length;

    // Headsign (clipped to area)
    for (let r = 0; r < 7; r++) {{
      for (let c = 0; c < Math.min(headsignBm[r].length, headsignAreaW); c++) {{
        if (headsignBm[r][c]) {{
          ctx.fillStyle = '#ffffff';
          ctx.fillRect((x + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                       PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
        }}
      }}
    }}
    x += headsignAreaW;

    // Live icon
    if (dep.live) {{
      const icon = getIconFrame(elapsed);
      const ix = x + 1;
      for (let r = 0; r < 6; r++) {{
        for (let c = 0; c < 6; c++) {{
          const val = icon[r][c];
          if (val === 2) {{
            ctx.fillStyle = '#ffffff';
            ctx.fillRect((ix + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                         PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
          }} else if (val === 1) {{
            ctx.fillStyle = dimColor();
            ctx.fillRect((ix + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                         PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
          }}
        }}
      }}
      x += 8;
    }}

    // Time
    for (let r = 0; r < 7; r++) {{
      for (let c = 0; c < timeBm[r].length; c++) {{
        if (timeBm[r][c]) {{
          ctx.fillStyle = timeColor;
          ctx.fillRect((x + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                       PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
        }}
      }}
    }}

    rowY += 11; // 7 rows + 4 spacer
  }}

  // Update info text
  const infoEl = document.getElementById('trip-info');
  const lines = deps.map(d => d.route + '  ' + d.headsign + '  ' + (d.live ? '\\u25c9' : '\\u25cb') + ' ' + (d.diff <= 0 ? 'Now' : d.diff + 'm'));
  infoEl.textContent = lines.join('  |  ');

  requestAnimationFrame(renderFrame);
}}

// ---- WebSocket ----
function getWsUrl() {{
  const sel = document.getElementById('endpoint-select').value;
  const custom = document.getElementById('custom-url');
  const wsProt = location.protocol === 'https:' ? 'wss://' : 'ws://';
  // Connect via /ws on the same origin (port 8080) — the web server
  // proxies to the internal WS server on :8000.  This avoids mixed-content
  // errors when the page is served over HTTPS (e.g. OrbStack .orb.local).
  if (sel === 'local') return wsProt + location.host + '/ws';
  if (sel === 'cloud') return 'wss://tt.horner.tj/';
  return custom.value || (wsProt + location.host + '/ws');
}}

function connect() {{
  const url = getWsUrl();
  const dot = document.getElementById('ws-dot');
  const label = document.getElementById('ws-label');

  if (ws) {{ try {{ ws.close(); }} catch(e) {{}} }}
  label.textContent = 'Connecting...';
  dot.className = 'status-dot';

  try {{
    ws = new WebSocket(url);
  }} catch(e) {{
    label.textContent = 'Error: ' + e.message;
    setTimeout(connect, 5000);
    return;
  }}

  ws.onopen = () => {{
    dot.className = 'status-dot connected';
    label.textContent = 'Connected to ' + url.replace('wss://', '').replace('ws://', '').split('/')[0];
    ws.send(JSON.stringify(SUBSCRIBE_PAYLOAD));
  }};

  ws.onmessage = (ev) => {{
    try {{
      const msg = JSON.parse(ev.data);
      if (msg.event === 'schedule') {{
        trips = (msg.data || {{}}).trips || [];
      }}
    }} catch(e) {{}}
  }};

  ws.onclose = () => {{
    dot.className = 'status-dot';
    label.textContent = 'Disconnected';
    setTimeout(connect, 3000);
  }};

  ws.onerror = () => {{
    dot.className = 'status-dot';
    label.textContent = 'Connection error';
  }};
}}

// ---- Endpoint selector ----
document.getElementById('endpoint-select').addEventListener('change', (e) => {{
  const custom = document.getElementById('custom-url');
  custom.style.display = e.target.value === 'custom' ? 'inline-block' : 'none';
  connect();
}});
document.getElementById('custom-url').addEventListener('change', connect);

// ---- Start ----
connect();
requestAnimationFrame(renderFrame);
</script>
</body>
</html>"""
