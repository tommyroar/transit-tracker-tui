import asyncio
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from .config import TransitConfig
from .logging import get_logger
from .metrics import metrics
from .transit_api import TransitAPI

log = get_logger("transit_tracker.web")


async def resolve_stop_coordinates(config: TransitConfig) -> List[Dict[str, Any]]:
    """Fetch lat/lon for all configured stops from the OBA API."""
    api = TransitAPI()
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
                self._serve_status()
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
        log.debug("%s", args[0], extra={"component": "web"})

    def _json_response(self, body: str):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _serve_status(self):
        """Serve live service state from the shared state file."""
        from .network.websocket_server import SERVICE_STATE_FILE
        try:
            if os.path.exists(SERVICE_STATE_FILE):
                with open(SERVICE_STATE_FILE, "r") as f:
                    state = json.load(f)
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


async def run_web(config: TransitConfig, host: str = "0.0.0.0", port: int = None):
    """Start the Transit Tracker web server with API spec and stop data."""
    if port is None:
        port = int(os.environ.get("PORT", 8080))

    log.info("Resolving stop coordinates...", extra={"component": "web"})
    stops = await resolve_stop_coordinates(config)
    if not stops:
        log.warning("No stops found in config. Add stops first with 'transit-tracker'.", extra={"component": "web"})
        return

    log.info("Resolved %d stops", len(stops), extra={"component": "web"})

    stops_json = json.dumps(stops, indent=2)
    spec_json = generate_api_spec(config)
    spec_html = generate_spec_html(spec_json)

    pages = [
        {
            "path": "/dashboard",
            "name": "Dashboard",
            "description": "Live metrics and observability dashboard",
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

    TransitWebHandler.routes = {
        "/": index_html,
        "/spec": spec_html,
        "/api/spec": spec_json,
        "/api/stops": stops_json,
    }
    # Dynamic routes — served fresh on each request
    TransitWebHandler.dynamic_routes = {"/api/status", "/api/metrics", "/api/logs", "/dashboard"}

    server = HTTPServer((host, port), TransitWebHandler)
    log.info("Transit Tracker web server at http://%s:%d", host, port, extra={"component": "web"})
    log.info("  /dashboard  — observability dashboard", extra={"component": "web"})
    log.info("  /spec       — API documentation page", extra={"component": "web"})
    log.info("  /api/metrics — metrics JSON endpoint", extra={"component": "web"})

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...", extra={"component": "web"})
        server.shutdown()


def generate_dashboard_html() -> str:
    """Generate a Datadog-inspired observability dashboard (single-page, dark theme)."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit Tracker — Dashboard</title>
<style>
  :root {
    --bg-primary: #131826;
    --bg-card: #1a1f36;
    --bg-card-hover: #232942;
    --border: #2d3555;
    --text-primary: #e4e7f1;
    --text-secondary: #8891b0;
    --text-muted: #5a6384;
    --accent-purple: #7c4dff;
    --accent-blue: #448aff;
    --accent-green: #00c853;
    --accent-orange: #ff9100;
    --accent-red: #ff1744;
    --accent-teal: #00bfa5;
    --accent-pink: #f50057;
    --chart-grid: #252b45;
    --scrollbar-bg: #1a1f36;
    --scrollbar-thumb: #3d4570;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Roboto', sans-serif;
    background: var(--bg-primary);
    color: var(--text-primary);
    line-height: 1.5;
    min-height: 100vh;
  }
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: var(--scrollbar-bg); }
  ::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 3px; }

  /* Header */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 16px 24px;
    border-bottom: 1px solid var(--border);
    background: var(--bg-card);
  }
  .header h1 { font-size: 18px; font-weight: 600; }
  .header h1 span { color: var(--accent-purple); }
  .header-right { display: flex; align-items: center; gap: 16px; }
  .status-dot {
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    background: var(--accent-green);
    animation: pulse 2s infinite;
  }
  .status-dot.error { background: var(--accent-red); }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
  .time-range {
    display: flex;
    background: var(--bg-primary);
    border-radius: 6px;
    overflow: hidden;
    border: 1px solid var(--border);
  }
  .time-range button {
    background: none;
    border: none;
    color: var(--text-secondary);
    padding: 4px 12px;
    font-size: 12px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .time-range button.active {
    background: var(--accent-purple);
    color: white;
  }
  .time-range button:hover:not(.active) { color: var(--text-primary); }

  /* Layout */
  .container { padding: 20px 24px; }
  .grid { display: grid; gap: 16px; margin-bottom: 16px; }
  .grid-4 { grid-template-columns: repeat(4, 1fr); }
  .grid-2 { grid-template-columns: repeat(2, 1fr); }
  .grid-1 { grid-template-columns: 1fr; }

  /* Stat cards */
  .stat-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
    transition: border-color 0.15s;
  }
  .stat-card:hover { border-color: var(--accent-purple); }
  .stat-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--text-muted); margin-bottom: 4px; }
  .stat-value { font-size: 28px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .stat-sub { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }
  .stat-value.green { color: var(--accent-green); }
  .stat-value.orange { color: var(--accent-orange); }
  .stat-value.red { color: var(--accent-red); }
  .stat-value.blue { color: var(--accent-blue); }
  .stat-value.purple { color: var(--accent-purple); }

  /* Chart cards */
  .chart-card {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 20px;
  }
  .chart-card h3 {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-secondary);
    margin-bottom: 12px;
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .chart-card h3 .dot {
    width: 6px; height: 6px;
    border-radius: 50%;
    display: inline-block;
  }
  canvas { width: 100% !important; height: 160px !important; }

  /* Log panel */
  .log-panel {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  .log-panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
    border-bottom: 1px solid var(--border);
  }
  .log-panel-header h3 { font-size: 13px; font-weight: 600; color: var(--text-secondary); }
  .log-filters {
    display: flex;
    gap: 6px;
  }
  .log-filters button {
    background: var(--bg-primary);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    padding: 2px 10px;
    border-radius: 4px;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s;
  }
  .log-filters button.active { background: var(--accent-purple); border-color: var(--accent-purple); color: white; }
  .log-filters button:hover:not(.active) { border-color: var(--text-muted); }
  .log-list {
    max-height: 360px;
    overflow-y: auto;
    font-family: 'SFMono-Regular', 'Menlo', 'Consolas', monospace;
    font-size: 12px;
    line-height: 1.7;
  }
  .log-entry {
    padding: 2px 20px;
    border-bottom: 1px solid #1e2340;
    display: flex;
    gap: 12px;
    transition: background 0.1s;
  }
  .log-entry:hover { background: var(--bg-card-hover); }
  .log-ts { color: var(--text-muted); white-space: nowrap; min-width: 80px; }
  .log-level {
    font-weight: 600;
    min-width: 44px;
    text-align: center;
    border-radius: 3px;
    padding: 0 4px;
  }
  .log-level.DEBUG { color: var(--text-muted); }
  .log-level.INFO { color: var(--accent-blue); }
  .log-level.WARNING { color: var(--accent-orange); }
  .log-level.ERROR { color: var(--accent-red); }
  .log-component { color: var(--accent-teal); min-width: 60px; }
  .log-msg { color: var(--text-primary); flex: 1; word-break: break-all; }

  @media (max-width: 900px) {
    .grid-4 { grid-template-columns: repeat(2, 1fr); }
    .grid-2 { grid-template-columns: 1fr; }
  }
  @media (max-width: 500px) {
    .grid-4 { grid-template-columns: 1fr; }
    .container { padding: 12px; }
  }
</style>
</head>
<body>

<div class="header">
  <h1><span>Transit Tracker</span> &mdash; Dashboard</h1>
  <div class="header-right">
    <div class="time-range">
      <button class="active" data-range="300">5m</button>
      <button data-range="900">15m</button>
      <button data-range="1800">30m</button>
      <button data-range="0">All</button>
    </div>
    <span id="conn-status"><span class="status-dot"></span> Live</span>
  </div>
</div>

<div class="container">
  <!-- Stat cards -->
  <div class="grid grid-4">
    <div class="stat-card">
      <div class="stat-label">Uptime</div>
      <div class="stat-value green" id="s-uptime">—</div>
      <div class="stat-sub" id="s-uptime-sub"></div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Active Clients</div>
      <div class="stat-value blue" id="s-clients">0</div>
      <div class="stat-sub" id="s-clients-sub"></div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Messages Sent</div>
      <div class="stat-value purple" id="s-msgs">0</div>
      <div class="stat-sub" id="s-msgs-sub"></div>
    </div>
    <div class="stat-card">
      <div class="stat-label">API Calls</div>
      <div class="stat-value" id="s-api">0</div>
      <div class="stat-sub" id="s-api-sub"></div>
    </div>
  </div>

  <div class="grid grid-4">
    <div class="stat-card">
      <div class="stat-label">Throttle Events (429s)</div>
      <div class="stat-value" id="s-throttle">0</div>
      <div class="stat-sub" id="s-throttle-sub"></div>
    </div>
    <div class="stat-card">
      <div class="stat-label">API Errors</div>
      <div class="stat-value" id="s-errors">0</div>
      <div class="stat-sub" id="s-errors-sub"></div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Refresh Interval</div>
      <div class="stat-value" id="s-interval">—</div>
      <div class="stat-sub" id="s-interval-sub"></div>
    </div>
    <div class="stat-card">
      <div class="stat-label">Cache Size</div>
      <div class="stat-value" id="s-cache">0</div>
      <div class="stat-sub">stops cached</div>
    </div>
  </div>

  <!-- Charts -->
  <div class="grid grid-2">
    <div class="chart-card">
      <h3><span class="dot" style="background:var(--accent-blue)"></span>API Latency (ms)</h3>
      <canvas id="chart-latency"></canvas>
    </div>
    <div class="chart-card">
      <h3><span class="dot" style="background:var(--accent-green)"></span>Active Clients</h3>
      <canvas id="chart-clients"></canvas>
    </div>
  </div>
  <div class="grid grid-2">
    <div class="chart-card">
      <h3><span class="dot" style="background:var(--accent-purple)"></span>Refresh Interval (s)</h3>
      <canvas id="chart-interval"></canvas>
    </div>
    <div class="chart-card">
      <h3><span class="dot" style="background:var(--accent-orange)"></span>Throttle Rate (%)</h3>
      <canvas id="chart-throttle"></canvas>
    </div>
  </div>

  <!-- Logs -->
  <div class="log-panel">
    <div class="log-panel-header">
      <h3>Event Log</h3>
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
// ---- Minimal chart renderer (no external deps) ----
class MiniChart {
  constructor(canvas, color) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.color = color;
    this.data = [];
    this._resize();
    window.addEventListener('resize', () => this._resize());
  }
  _resize() {
    const r = this.canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = r.width * dpr;
    this.canvas.height = 160 * dpr;
    this.ctx.scale(dpr, dpr);
    this.w = r.width;
    this.h = 160;
    this.draw();
  }
  setData(points) { this.data = points; this.draw(); }
  draw() {
    const ctx = this.ctx, w = this.w, h = this.h, d = this.data;
    ctx.clearRect(0, 0, w, h);
    if (d.length < 2) {
      ctx.fillStyle = '#3d4570';
      ctx.font = '12px sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Waiting for data...', w/2, h/2);
      return;
    }
    // Grid lines
    ctx.strokeStyle = '#252b45';
    ctx.lineWidth = 1;
    for (let i = 1; i < 4; i++) {
      const y = (h / 4) * i;
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    // Data
    const vals = d.map(p => p[1]);
    let min = Math.min(...vals), max = Math.max(...vals);
    if (max === min) { max = min + 1; }
    const pad = 8;
    const innerH = h - pad * 2;
    const xStep = w / (d.length - 1);
    // Area fill
    ctx.beginPath();
    ctx.moveTo(0, h);
    for (let i = 0; i < d.length; i++) {
      const x = i * xStep;
      const y = pad + innerH - ((vals[i] - min) / (max - min)) * innerH;
      ctx.lineTo(x, y);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    const grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, this.color + '40');
    grad.addColorStop(1, this.color + '05');
    ctx.fillStyle = grad;
    ctx.fill();
    // Line
    ctx.beginPath();
    for (let i = 0; i < d.length; i++) {
      const x = i * xStep;
      const y = pad + innerH - ((vals[i] - min) / (max - min)) * innerH;
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = this.color;
    ctx.lineWidth = 2;
    ctx.stroke();
    // Labels
    ctx.fillStyle = '#5a6384';
    ctx.font = '10px sans-serif';
    ctx.textAlign = 'left';
    ctx.fillText(max.toFixed(1), 4, pad + 10);
    ctx.fillText(min.toFixed(1), 4, h - 4);
    // Latest value
    if (d.length > 0) {
      const last = vals[vals.length - 1];
      ctx.fillStyle = this.color;
      ctx.font = 'bold 12px sans-serif';
      ctx.textAlign = 'right';
      ctx.fillText(last.toFixed(1), w - 4, pad + 10);
    }
  }
}

// ---- Init charts ----
const charts = {
  latency: new MiniChart(document.getElementById('chart-latency'), '#448aff'),
  clients: new MiniChart(document.getElementById('chart-clients'), '#00c853'),
  interval: new MiniChart(document.getElementById('chart-interval'), '#7c4dff'),
  throttle: new MiniChart(document.getElementById('chart-throttle'), '#ff9100'),
};

// ---- State ----
let timeRange = 300; // seconds
let logFilter = 'all';
let lastLogTs = 0;
let allLogs = [];

// ---- Time range buttons ----
document.querySelectorAll('.time-range button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.time-range button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    timeRange = parseInt(btn.dataset.range);
    fetchMetrics();
  });
});

// ---- Log filter buttons ----
document.querySelectorAll('.log-filters button').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.log-filters button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    logFilter = btn.dataset.level;
    renderLogs();
  });
});

// ---- Formatting helpers ----
function fmtUptime(s) {
  if (s < 60) return Math.floor(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h + 'h ' + m + 'm';
}
function fmtNum(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return n.toString();
}
function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'});
}

// ---- Fetch & render ----
async function fetchMetrics() {
  try {
    const since = timeRange > 0 ? (Date.now() / 1000 - timeRange) : 0;
    const res = await fetch('/api/metrics?since=' + since);
    const m = await res.json();

    // Stat cards
    document.getElementById('s-uptime').textContent = fmtUptime(m.uptime_s);
    const startDate = new Date((m.ts - m.uptime_s) * 1000);
    document.getElementById('s-uptime-sub').textContent = 'since ' + startDate.toLocaleTimeString();

    document.getElementById('s-clients').textContent = m.gauges.active_clients;
    document.getElementById('s-clients-sub').textContent =
      fmtNum(m.counters.ws_connections) + ' total connections';

    document.getElementById('s-msgs').textContent = fmtNum(m.counters.messages_sent);
    document.getElementById('s-msgs-sub').textContent =
      fmtNum(m.counters.messages_received) + ' received';

    const apiCalls = m.counters.api_calls;
    document.getElementById('s-api').textContent = fmtNum(apiCalls);
    const errRate = apiCalls > 0 ? (m.counters.api_errors / apiCalls * 100).toFixed(1) : '0.0';
    document.getElementById('s-api-sub').textContent = errRate + '% error rate';

    const throttle = m.counters.throttle_events;
    const tEl = document.getElementById('s-throttle');
    tEl.textContent = fmtNum(throttle);
    tEl.className = 'stat-value ' + (throttle > 0 ? 'orange' : 'green');
    const throttleRate = apiCalls > 0 ? (throttle / apiCalls * 100).toFixed(1) : '0.0';
    document.getElementById('s-throttle-sub').textContent = throttleRate + '% of API calls';

    const errEl = document.getElementById('s-errors');
    errEl.textContent = fmtNum(m.counters.api_errors);
    errEl.className = 'stat-value ' + (m.counters.api_errors > 0 ? 'red' : 'green');
    document.getElementById('s-errors-sub').textContent =
      fmtNum(m.counters.api_errors) + ' total errors';

    const intEl = document.getElementById('s-interval');
    intEl.textContent = m.gauges.refresh_interval_s + 's';
    intEl.className = 'stat-value ' + (m.gauges.refresh_interval_s > 60 ? 'orange' : '');
    document.getElementById('s-interval-sub').textContent = 'base: polling cycle';

    document.getElementById('s-cache').textContent = m.gauges.cache_size;

    // Charts
    charts.latency.setData(m.series.api_latency_ms);
    charts.clients.setData(m.series.active_clients);
    charts.interval.setData(m.series.refresh_interval_s);
    charts.throttle.setData(m.series.throttle_rate);

    // Connection status
    document.querySelector('.status-dot').className = 'status-dot';
  } catch (e) {
    document.querySelector('.status-dot').className = 'status-dot error';
  }
}

async function fetchLogs() {
  try {
    const res = await fetch('/api/logs?since=' + lastLogTs + '&limit=200');
    const data = await res.json();
    if (data.logs && data.logs.length > 0) {
      allLogs = allLogs.concat(data.logs);
      // Keep only last 500
      if (allLogs.length > 500) allLogs = allLogs.slice(-500);
      lastLogTs = data.logs[data.logs.length - 1].ts + 0.001;
      renderLogs();
    }
  } catch (e) {}
}

function renderLogs() {
  const list = document.getElementById('log-list');
  let filtered = allLogs;
  if (logFilter !== 'all') {
    filtered = allLogs.filter(e => e.level === logFilter);
  }
  // Show latest 200
  const visible = filtered.slice(-200);
  list.innerHTML = visible.map(e => {
    const ts = fmtTime(e.ts);
    const level = e.level || 'INFO';
    const component = e.component || e.logger || '';
    const msg = e.msg || '';
    return `<div class="log-entry">
      <span class="log-ts">${ts}</span>
      <span class="log-level ${level}">${level}</span>
      <span class="log-component">${component}</span>
      <span class="log-msg">${escHtml(msg)}</span>
    </div>`;
  }).join('');
  // Auto-scroll to bottom
  list.scrollTop = list.scrollHeight;
}

function escHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// ---- Poll loop ----
fetchMetrics();
fetchLogs();
setInterval(fetchMetrics, 2000);
setInterval(fetchLogs, 2000);
</script>
</body>
</html>"""
