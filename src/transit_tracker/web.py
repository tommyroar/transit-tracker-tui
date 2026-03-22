import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import parse_qs, urlparse

from .config import TransitConfig, build_route_stop_pairs
from .logging import get_logger
from .metrics import metrics
from .transit_api import TransitAPI

log = get_logger("transit_tracker.web")

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(name: str) -> str:
    """Read an HTML template from the templates directory."""
    return (_TEMPLATES_DIR / name).read_text()


# ── HTML table helpers ───────────────────────────────────────────────


def _html_table_rows(items: list, columns: list[str]) -> str:
    """Build HTML <tr> rows from a list of dicts.

    Each *column* is a key into the dict.  Values are wrapped in <code>
    if the column name starts with ``code:``, e.g. ``"code:feed"``.
    """
    rows = ""
    for item in items:
        cells = ""
        for col in columns:
            if col.startswith("code:"):
                key = col[5:]
                cells += f"<td><code>{item[key]}</code></td>"
            else:
                cells += f"<td>{item[col]}</td>"
        rows += f"<tr>{cells}</tr>\n"
    return rows


# ── Stop coordinates ─────────────────────────────────────────────────


async def resolve_stop_coordinates(
    config: TransitConfig,
) -> List[Dict[str, Any]]:
    """Fetch lat/lon for all configured stops from the OBA API."""
    api = TransitAPI(oba_api_key=config.service.oba_api_key)
    try:
        tasks = [api.get_stop(stop.stop_id) for stop in config.transit_tracker.stops]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        stops = []
        for stop_cfg, result in zip(config.transit_tracker.stops, results, strict=True):
            if isinstance(result, Exception):
                log.warning(
                    "Could not fetch stop %s: %s",
                    stop_cfg.stop_id,
                    result,
                    extra={"component": "web"},
                )
                continue
            if result is None:
                log.warning(
                    "Stop %s not found",
                    stop_cfg.stop_id,
                    extra={"component": "web"},
                )
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


# ── API spec (JSON) ──────────────────────────────────────────────────


def generate_api_spec(config: TransitConfig) -> str:
    """Generate a JSON API specification with example payloads."""
    pairs_str = build_route_stop_pairs(config.subscriptions)

    # Build example trips from config subscriptions
    example_bus_trips: list[dict] = []
    example_ferry_trips: list[dict] = []
    now_ts = 1773534000  # static example timestamp

    for i, sub in enumerate(config.subscriptions):
        is_ferry = sub.route.startswith("wsf:") or sub.route.startswith("95_")
        trip = {
            "tripId": f"{'95' if is_ferry else '40'}_{141500000 + i}",
            "routeId": sub.route.replace("st:", "").replace("wsf:", "95_"),
            "routeName": (
                sub.label.split(" - ")[0] if " - " in sub.label else sub.label
            ),
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

    _default_bus = [
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
    ]
    _default_ferry = [
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
    ]

    spec = {
        "info": {
            "title": "Transit Tracker WebSocket API",
            "description": (
                "JSON-over-WebSocket protocol for real-time transit arrivals. "
                "Compatible with the TJ Horner transit-tracker-api and "
                "ESP32 LED matrix firmware."
            ),
            "version": "1.0.0",
            "websocket_url": config.api_url or "ws://localhost:8000",
            "web_url": "http://localhost:8080",
        },
        "config": {
            "check_interval_seconds": (config.service.check_interval_seconds),
            "arrival_threshold_minutes": (config.service.arrival_threshold_minutes),
            "time_display": config.transit_tracker.time_display,
            "num_panels": config.service.num_panels,
            "panel_size": (
                f"{config.service.panel_width}x{config.service.panel_height}"
            ),
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
                    "description": (
                        "Subscribe to arrival updates for route/stop pairs."
                    ),
                    "fields": {
                        "event": {
                            "type": "string",
                            "value": "schedule:subscribe",
                        },
                        "client_name": {
                            "type": "string",
                            "optional": True,
                            "description": ("Friendly name for server dashboard"),
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
                                "Semicolon-separated entries: "
                                "routeId,stopId[,offsetSec]. "
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
                        f"(every "
                        f"{config.service.check_interval_seconds}s)."
                    ),
                    "fields": {
                        "event": {
                            "type": "string",
                            "value": "schedule",
                        },
                        "data.trips": {
                            "type": "array<Trip>",
                            "description": (
                                "Sorted by arrivalTime ascending, "
                                "capped by client limit"
                            ),
                        },
                    },
                    "examples": {
                        "bus": {
                            "event": "schedule",
                            "data": {
                                "trips": (
                                    example_bus_trips[:2]
                                    if example_bus_trips
                                    else _default_bus
                                ),
                            },
                        },
                        "ferry": {
                            "event": "schedule",
                            "data": {
                                "trips": (
                                    example_ferry_trips[:2]
                                    if example_ferry_trips
                                    else _default_ferry
                                ),
                            },
                        },
                    },
                },
                "heartbeat": {
                    "description": (
                        "Sent every 10 seconds to keep the connection alive."
                    ),
                    "example": {"event": "heartbeat", "data": None},
                },
            },
        },
        "types": {
            "Trip": {
                "tripId": {
                    "type": "string",
                    "description": "OBA trip identifier",
                },
                "routeId": {
                    "type": "string",
                    "description": ("OBA route ID (e.g., 40_100240, 95_73)"),
                },
                "routeName": {
                    "type": "string",
                    "description": ("Short route name (e.g., '554', 'SEA-BI')"),
                },
                "routeColor": {
                    "type": "string|null",
                    "description": ("Hex color without # (e.g., 'BF34A4'), or null"),
                },
                "stopId": {
                    "type": "string",
                    "description": (
                        "Stop ID as subscribed (preserves st:/wsf: prefix)"
                    ),
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
                        "Unix timestamp (seconds) — adjusted for "
                        "time_offset and arrival/departure mode. "
                        "ESP32 computes: "
                        "display_mins = (arrivalTime - now()) / 60"
                    ),
                },
                "departureTime": {
                    "type": "int",
                    "description": (
                        "Unix timestamp (seconds) — departure time with offset"
                    ),
                },
                "isRealtime": {
                    "type": "bool",
                    "description": ("true if GPS-predicted, false if scheduled"),
                },
            },
        },
        "id_prefixes": {
            "st:": (
                "Sound Transit / King County Metro "
                "(stripped to OBA format, e.g., st:1_8494 → 1_8494)"
            ),
            "wsf:": (
                "Washington State Ferries (mapped to agency 95, e.g., wsf:7 → 95_7)"
            ),
        },
        "ferry": {
            "vessel_mapping": (
                "When vehicleId is present, headsign is replaced "
                "with vessel name from WSF_VESSELS dict"
            ),
            "arrival_vs_departure": (
                "Determined per-trip by OBA "
                "arrivalEnabled/departureEnabled flags. "
                "Origin docks show departure time; "
                "destination docks show arrival time."
            ),
            "direction_filtering": (
                "Ferry trips are filtered by direction at the terminal. "
                "At a departure terminal (display_mode='departure'), "
                "inbound arrivals "
                "(arrivalEnabled=True, departureEnabled=False) are "
                "skipped, and vice versa. "
                "Unlike buses, ferries with an expired preferred time "
                "are dropped entirely "
                "rather than falling back to the alternate time."
            ),
            "realtime_detection": (
                "Ferry isRealtime is based on vehicleId presence in "
                "OBA data, not the predicted flag used for buses. "
                "A ferry trip is realtime "
                "only when a vessel is actively tracked."
            ),
            "abbreviations": [
                {"original": a.original, "short": a.short}
                for a in config.transit_tracker.abbreviations
            ],
        },
        "rate_limiting": {
            "backoff": (
                "On HTTP 429, refresh interval doubles (max 600s). "
                "Recovers 20% per successful cycle."
            ),
            "per_stop_cooldown": (
                "Rate-limited stops have individual cooldown timestamps."
            ),
        },
    }

    return json.dumps(spec, indent=2)


# ── Spec HTML page ───────────────────────────────────────────────────


def _build_spec_table_fragments(spec: dict) -> dict:
    """Build all HTML table fragments needed by the spec template."""
    config = spec["config"]
    messages = spec["messages"]
    types = spec["types"]
    ferry = spec.get("ferry", {})

    jb = lambda obj: json.dumps(obj, indent=2)  # noqa: E731

    # Subscription rows
    sub_rows = _html_table_rows(
        config["subscriptions"],
        ["code:feed", "code:route", "code:stop", "label", "time_offset"],
    )

    # Trip type field rows
    trip_rows = ""
    for field, meta in types.get("Trip", {}).items():
        trip_rows += (
            f"<tr><td><code>{field}</code></td>"
            f"<td><code>{meta['type']}</code></td>"
            f"<td>{meta['description']}</td></tr>\n"
        )

    # ID prefix rows
    prefix_rows = ""
    for prefix, desc in spec.get("id_prefixes", {}).items():
        prefix_rows += f"<tr><td><code>{prefix}</code></td><td>{desc}</td></tr>\n"

    # Subscribe fields rows
    sub_msg = messages["client_to_server"]["schedule:subscribe"]
    sub_fields_rows = ""
    for fname, fmeta in sub_msg["fields"].items():
        opt = " <em>(optional)</em>" if fmeta.get("optional") else ""
        default = (
            f" default: <code>{fmeta['default']}</code>" if "default" in fmeta else ""
        )
        desc = fmeta.get("description", fmeta.get("value", ""))
        sub_fields_rows += (
            f"<tr><td><code>{fname}</code></td>"
            f"<td><code>{fmeta['type']}</code>{opt}{default}</td>"
            f"<td>{desc}</td></tr>\n"
        )

    # Abbreviation rows
    abbr_rows = ""
    for a in ferry.get("abbreviations", []):
        abbr_rows += (
            f"<tr><td>{a['original']}</td><td><code>{a['short']}</code></td></tr>\n"
        )
    abbreviations_section = (
        "<h3>Route Abbreviations</h3>"
        "<table><tr><th>Original</th><th>Short</th></tr>" + abbr_rows + "</table>"
        if abbr_rows
        else ""
    )

    sched_msg = messages["server_to_client"]["schedule"]

    return {
        "sub_rows": sub_rows,
        "trip_rows": trip_rows,
        "prefix_rows": prefix_rows,
        "sub_fields_rows": sub_fields_rows,
        "abbreviations_section": abbreviations_section,
        "subscribe_description": sub_msg["description"],
        "subscribe_example": jb(sub_msg["example"]),
        "schedule_description": sched_msg["description"],
        "bus_example": jb(sched_msg["examples"]["bus"]),
        "ferry_example": jb(sched_msg["examples"]["ferry"]),
        "heartbeat_example": jb(messages["server_to_client"]["heartbeat"]["example"]),
    }


def generate_spec_html(spec_json: str) -> str:
    """Generate a styled HTML documentation page from the API spec JSON."""
    spec = json.loads(spec_json)
    info = spec["info"]
    config = spec["config"]
    ferry = spec.get("ferry", {})
    rate = spec.get("rate_limiting", {})

    fragments = _build_spec_table_fragments(spec)

    template = _load_template("spec.html")
    return template.format_map(
        {
            # Info
            "title": info["title"],
            "websocket_url": info["websocket_url"],
            "version": info["version"],
            "description": info["description"],
            # Config
            "check_interval_seconds": config["check_interval_seconds"],
            "arrival_threshold_minutes": config["arrival_threshold_minutes"],
            "time_display": config["time_display"],
            "num_panels": config["num_panels"],
            "panel_size": config["panel_size"],
            "scroll_headsigns": config["scroll_headsigns"],
            # Ferry
            "vessel_mapping": ferry.get("vessel_mapping", "N/A"),
            "arrival_vs_departure": ferry.get("arrival_vs_departure", "N/A"),
            "direction_filtering": ferry.get("direction_filtering", "N/A"),
            "realtime_detection": ferry.get("realtime_detection", "N/A"),
            # Rate limiting
            "backoff": rate.get("backoff", "N/A"),
            "per_stop_cooldown": rate.get("per_stop_cooldown", "N/A"),
            # Table fragments
            **fragments,
        }
    )


# ── Index page ───────────────────────────────────────────────────────


def generate_index_html(pages: List[Dict[str, str]]) -> str:
    """Generate an index page listing available web pages."""
    links = "".join(
        f'<li><a href="{p["path"]}">{p["name"]}</a> — {p["description"]}</li>'
        for p in pages
    )
    return _load_template("index.html").format_map({"links": links})


# ── Static template loaders ──────────────────────────────────────────


def generate_monitor_html() -> str:
    """Load the live network topology monitor page."""
    return _load_template("monitor.html")


def generate_dashboard_html() -> str:
    """Load the observability dashboard page."""
    return _load_template("dashboard.html")


# ── HTTP handler ─────────────────────────────────────────────────────


class TransitWebHandler(BaseHTTPRequestHandler):
    """HTTP request handler with static and dynamic route dispatch."""

    routes: Dict[str, str] = {}
    dynamic_routes: set = set()

    # Maps dynamic path → (method_name, needs_query)
    _DYNAMIC_DISPATCH: Dict[str, tuple] = {
        "/api/status": ("_serve_status", True),
        "/api/metrics": ("_serve_metrics", True),
        "/api/logs": ("_serve_logs", True),
        "/api/dimming": ("_serve_dimming_get", False),
        "/dashboard": ("_serve_template", False),
        "/monitor": ("_serve_template", False),
    }

    # Template pages served with no-cache
    _TEMPLATE_PAGES: Dict[str, str] = {
        "/dashboard": "dashboard.html",
        "/monitor": "monitor.html",
    }

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        # Dynamic routes
        if path in self.dynamic_routes:
            dispatch = self._DYNAMIC_DISPATCH.get(path)
            if dispatch:
                method_name, needs_query = dispatch
                if path in self._TEMPLATE_PAGES:
                    self._serve_template(path)
                elif needs_query:
                    getattr(self, method_name)(query)
                else:
                    getattr(self, method_name)()
                return

        # Static routes
        content = self.routes.get(path)
        if content is not None:
            content_type = (
                "application/json" if path.startswith("/api/") else "text/html"
            )
            self._send(200, content, content_type)
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

    # ── Response helpers ──

    def _send(
        self,
        code: int,
        body: str,
        content_type: str = "text/html",
        *,
        cache: str | None = None,
    ):
        """Send a complete response with CORS headers."""
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _json_response(self, body: str):
        self._send(200, body, "application/json")

    def _json_error(self, code: int, message: str):
        self._send(
            code,
            json.dumps({"error": message}),
            "application/json",
        )

    # ── Dynamic route handlers ──

    def _serve_template(self, path: str = "/dashboard"):
        """Serve an HTML template page with no-cache."""
        template_name = self._TEMPLATE_PAGES.get(path, "dashboard.html")
        html = _load_template(template_name)
        self._send(200, html, "text/html", cache="no-cache")

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

    def _serve_dimming_get(self):
        """Return the current dimming schedule from service settings."""
        from .config import load_service_settings

        settings = load_service_settings()
        self._json_response(
            json.dumps(
                {
                    "dimming_schedule": [
                        e.model_dump() for e in settings.dimming_schedule
                    ],
                    "display_brightness": settings.display_brightness,
                    "device_ip": settings.device_ip,
                }
            )
        )

    def _handle_dimming_post(self):
        """Update dimming schedule via REST, persisting to service.yaml."""
        from .config import (
            DimmingEntry,
            load_service_settings,
            save_service_settings,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            self._json_error(400, f"Invalid JSON: {e}")
            return

        try:
            entries = [
                DimmingEntry.model_validate(e) for e in data.get("dimming_schedule", [])
            ]
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

        self._json_response(
            json.dumps(
                {
                    "status": "ok",
                    "dimming_schedule": [e.model_dump() for e in entries],
                    "message": ("Schedule saved. Will take effect within 60 seconds."),
                }
            )
        )


# ── Server entrypoint ────────────────────────────────────────────────


_WEB_PAGES = [
    {
        "path": "/dashboard",
        "name": "Dashboard",
        "description": "Live metrics and observability dashboard",
    },
    {
        "path": "/monitor",
        "name": "Network Monitor",
        "description": (
            "Live topology diagram showing proxy, provider, and connected displays"
        ),
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
        "description": ("Live service state (clients, rate limits, uptime)"),
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


async def run_web(
    config: TransitConfig,
    host: str = "0.0.0.0",
    port: int = None,
):
    """Start the Transit Tracker web server with API spec and stop data."""
    if port is None:
        port = int(os.environ.get("PORT", 8080))

    log.info("Resolving stop coordinates...", extra={"component": "web"})
    stops = await resolve_stop_coordinates(config)
    if not stops:
        log.warning(
            "No stops resolved — serving with empty stop data",
            extra={"component": "web"},
        )
        stops = []
    else:
        log.info(
            "Resolved %d stops",
            len(stops),
            extra={"component": "web"},
        )

    stops_json = json.dumps(stops, indent=2)
    spec_json = generate_api_spec(config)
    spec_html = generate_spec_html(spec_json)
    index_html = generate_index_html(_WEB_PAGES)

    TransitWebHandler.routes = {
        "/": index_html,
        "/spec": spec_html,
        "/api/spec": spec_json,
        "/api/stops": stops_json,
    }
    TransitWebHandler.dynamic_routes = {
        "/api/status",
        "/api/metrics",
        "/api/logs",
        "/api/dimming",
        "/dashboard",
        "/monitor",
    }

    server = HTTPServer((host, port), TransitWebHandler)
    log.info(
        "Transit Tracker web server at http://%s:%d",
        host,
        port,
        extra={"component": "web"},
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...", extra={"component": "web"})
        server.shutdown()
