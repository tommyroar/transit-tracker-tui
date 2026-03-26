"""Web server entry point and HTTP routing for Transit Tracker.

Contains ``run_web`` (the async server), ``TransitWebHandler`` (the
legacy ``BaseHTTPRequestHandler`` used by tests), and all request
dispatch logic.
"""

import asyncio
import json
import os
from http.server import BaseHTTPRequestHandler
from typing import Dict
from urllib.parse import parse_qs, urlparse

import websockets

from ..config import TransitConfig
from ..logging import get_logger
from ..metrics import metrics
from .api_handlers import (
    _handle_arrivals,
    _handle_config_save,
    _handle_config_settings_get,
    _handle_config_settings_patch,
    _handle_config_stops_delete,
    _handle_config_stops_get,
    _handle_config_stops_post,
    _handle_dimming_set,
    _handle_geocode,
    _handle_profile_activate,
    _handle_profiles_list,
    _handle_routes_for_location,
    _handle_stops_for_route,
    resolve_stop_coordinates,
)
from .pages import (
    generate_dashboard_html,
    generate_index_html,
    generate_monitor_html,
    generate_simulator_html,
)
from .spec import generate_api_spec, generate_spec_html

log = get_logger("transit_tracker.web")

# All routes are served under this prefix
PREFIX = "/transit-tracker"

# Daylight dimming fields accepted via POST /api/dimming
_DAYLIGHT_FIELDS: Dict[str, type] = {
    "daylight_dimming_enabled": bool,
    "daylight_dimming_timezone": str,
    "daylight_latitude": float,
    "daylight_longitude": float,
    "dawn_ramp_minutes": int,
    "dawn_ramp_steps": int,
    "dusk_ramp_minutes": int,
    "dusk_ramp_steps": int,
}


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
            if path == f"{PREFIX}/api/status":
                self._serve_status(query)
                return
            if path == f"{PREFIX}/api/metrics":
                self._serve_metrics(query)
                return
            if path == f"{PREFIX}/api/logs":
                self._serve_logs(query)
                return
            if path == f"{PREFIX}/dashboard":
                self._serve_dashboard()
                return
            if path == f"{PREFIX}/monitor":
                self._serve_monitor()
                return
            if path == f"{PREFIX}/api/dimming":
                self._serve_dimming_get()
                return
            if path == f"{PREFIX}/api/dimming/set":
                try:
                    status, resp = _handle_dimming_set(query)
                    self._json_response(json.dumps(resp), status)
                except Exception as e:
                    self._json_error(400, str(e))
                return
            if path == f"{PREFIX}/simulator":
                self._serve_simulator()
                return
            if path == f"{PREFIX}/api/profiles":
                self._json_response(
                    json.dumps(_handle_profiles_list())
                )
                return
            if path == f"{PREFIX}/api/profile/activate":
                status, resp = _handle_profile_activate(query)
                self._json_response(json.dumps(resp), status)
                return
            if path == f"{PREFIX}/api/config/stops":
                self._json_response(
                    json.dumps(_handle_config_stops_get())
                )
                return
            if path == f"{PREFIX}/api/config/settings":
                self._json_response(
                    json.dumps(_handle_config_settings_get())
                )
                return

        content = self.routes.get(path)
        if content is not None:
            content_type = (
                "application/json" if "/api/" in path else "text/html"
            )
            self.send_response(200)
            self.send_header(
                "Content-Type", f"{content_type}; charset=utf-8"
            )
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

        if path == f"{PREFIX}/api/dimming":
            self._handle_dimming_post()
            return

        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h1>404 Not Found</h1>")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        log.debug("%s", args[0], extra={"component": "web"})

    def _json_response(self, body: str, status: int = 200):
        self.send_response(status)
        self.send_header(
            "Content-Type", "application/json; charset=utf-8"
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def _serve_status(self, query: dict = None):
        """Serve live service state from the shared state file."""
        from ..network.websocket_server import SERVICE_STATE_FILE

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
        self.send_header(
            "Content-Type", "application/json; charset=utf-8"
        )
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(
            json.dumps({"error": message}).encode("utf-8")
        )

    def _serve_dimming_get(self):
        """Return the current daylight dimming settings."""
        import datetime as _dt
        from ..config import build_daylight_schedule, load_service_settings

        settings = load_service_settings()
        resp = {
            "daylight_dimming_enabled": settings.daylight_dimming_enabled,
            "daylight_dimming_timezone": settings.daylight_dimming_timezone,
            "dawn_ramp_minutes": settings.dawn_ramp_minutes,
            "dawn_ramp_steps": settings.dawn_ramp_steps,
            "dusk_ramp_minutes": settings.dusk_ramp_minutes,
            "dusk_ramp_steps": settings.dusk_ramp_steps,
            "display_brightness": settings.display_brightness,
            "device_ip": settings.device_ip,
        }
        if settings.daylight_dimming_enabled:
            schedule = build_daylight_schedule(
                dt=_dt.date.today(),
                timezone=settings.daylight_dimming_timezone,
                dawn_ramp_minutes=settings.dawn_ramp_minutes,
                dawn_ramp_steps=settings.dawn_ramp_steps,
                dusk_ramp_minutes=settings.dusk_ramp_minutes,
                dusk_ramp_steps=settings.dusk_ramp_steps,
            )
            resp["computed_schedule"] = [e.model_dump() for e in schedule]
        self._json_response(json.dumps(resp))

    def _handle_dimming_post(self):
        """Update daylight dimming settings via REST, persisting to service.yaml."""
        from ..config import (
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

        settings = load_service_settings()
        updates = {}
        for field, typ in _DAYLIGHT_FIELDS.items():
            if field in data:
                updates[field] = typ(data[field])
        if "device_ip" in data:
            updates["device_ip"] = data["device_ip"]
        if "display_brightness" in data:
            updates["display_brightness"] = int(data["display_brightness"])
        if updates:
            from ..config import ServiceSettings
            settings = ServiceSettings.model_validate(
                {**settings.model_dump(), **updates}
            )
        save_service_settings(settings)

        self._json_response(
            json.dumps(
                {
                    "status": "ok",
                    "daylight_dimming_enabled": settings.daylight_dimming_enabled,
                    "message": "Dimming settings saved. Will take effect within 60 seconds.",
                }
            )
        )

    def _serve_simulator(self):
        """Serve the web LED simulator HTML."""
        html = generate_simulator_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))


async def run_web(
    config: TransitConfig, host: str = "0.0.0.0", port: int = None
):
    """Start the Transit Tracker web server with API spec and stop data."""
    if port is None:
        port = int(os.environ.get("PORT", 8080))

    log.info(
        "Resolving stop coordinates...", extra={"component": "web"}
    )
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

    pages = [
        {
            "path": f"{PREFIX}/simulator",
            "name": "LED Simulator",
            "description": "Browser-based HUB75 LED matrix emulator with live data",
        },
        {
            "path": f"{PREFIX}/dashboard",
            "name": "Dashboard",
            "description": "Live metrics and observability dashboard",
        },
        {
            "path": f"{PREFIX}/monitor",
            "name": "Network Monitor",
            "description": "Live topology showing proxy, provider, and displays",
        },
        {
            "path": f"{PREFIX}/spec",
            "name": "API Docs",
            "description": "Interactive WebSocket API documentation",
        },
        {
            "path": f"{PREFIX}/api/spec",
            "name": "API Spec (JSON)",
            "description": "Raw JSON specification with example payloads",
        },
        {
            "path": f"{PREFIX}/api/stops",
            "name": "Stops",
            "description": "Configured stop coordinates as JSON",
        },
        {
            "path": f"{PREFIX}/api/status",
            "name": "Status",
            "description": "Live service state (clients, rate limits, uptime)",
        },
        {
            "path": f"{PREFIX}/api/metrics",
            "name": "Metrics",
            "description": "Time-series metrics snapshot (JSON)",
        },
        {
            "path": f"{PREFIX}/api/logs",
            "name": "Logs",
            "description": "Recent log entries from ring buffer (JSON)",
        },
    ]
    index_html = generate_index_html(pages)

    # -- Route tables for the dual HTTP+WS server --
    static_routes = {
        f"{PREFIX}": index_html,
        f"{PREFIX}/spec": spec_html,
        f"{PREFIX}/api/spec": spec_json,
        f"{PREFIX}/api/stops": stops_json,
    }
    dynamic_routes = {
        f"{PREFIX}/api/status",
        f"{PREFIX}/api/metrics",
        f"{PREFIX}/api/logs",
        f"{PREFIX}/api/dimming",
        f"{PREFIX}/api/dimming/set",
        f"{PREFIX}/api/profiles",
        f"{PREFIX}/api/profile/activate",
        f"{PREFIX}/api/geocode",
        f"{PREFIX}/api/routes",
        f"{PREFIX}/api/arrivals",
        f"{PREFIX}/api/config/stops",
        f"{PREFIX}/api/config/save",
        f"{PREFIX}/api/config/settings",
        f"{PREFIX}/dashboard",
        f"{PREFIX}/monitor",
        f"{PREFIX}/simulator",
    }
    # Route patterns that need path parameter extraction
    route_pattern_prefix = f"{PREFIX}/api/routes/"

    # -- Also configure the legacy HTTPServer routes (used by tests) --
    TransitWebHandler.routes = static_routes
    TransitWebHandler.dynamic_routes = dynamic_routes

    def _serve_dynamic(path: str, query: dict) -> tuple:
        """Serve a dynamic route, returning (status, content_type, body)."""
        from ..network.websocket_server import SERVICE_STATE_FILE

        if path == f"{PREFIX}/api/status":
            include_full = query.get("full", ["0"])[0] == "1"
            try:
                if os.path.exists(SERVICE_STATE_FILE):
                    with open(SERVICE_STATE_FILE, "r") as f:
                        state = json.load(f)
                    if not include_full:
                        state.pop("last_message", None)
                    return (
                        200,
                        "application/json",
                        json.dumps(state),
                    )
                return (
                    200,
                    "application/json",
                    json.dumps({"status": "unavailable"}),
                )
            except Exception:
                return (
                    200,
                    "application/json",
                    json.dumps({"status": "error"}),
                )

        if path == f"{PREFIX}/api/metrics":
            since = float(query.get("since", [0])[0])
            return (
                200,
                "application/json",
                json.dumps(metrics.snapshot(series_since=since)),
            )

        if path == f"{PREFIX}/api/logs":
            since = float(query.get("since", [0])[0])
            limit = int(query.get("limit", [200])[0])
            entries = metrics.logs.snapshot(since=since, limit=limit)
            return (
                200,
                "application/json",
                json.dumps({"logs": entries}),
            )

        if path == f"{PREFIX}/api/dimming":
            import datetime as _dt
            from ..config import build_daylight_schedule, load_service_settings

            settings = load_service_settings()
            resp = {
                "daylight_dimming_enabled": settings.daylight_dimming_enabled,
                "daylight_dimming_timezone": settings.daylight_dimming_timezone,
                "dawn_ramp_minutes": settings.dawn_ramp_minutes,
                "dawn_ramp_steps": settings.dawn_ramp_steps,
                "dusk_ramp_minutes": settings.dusk_ramp_minutes,
                "dusk_ramp_steps": settings.dusk_ramp_steps,
                "display_brightness": settings.display_brightness,
                "device_ip": settings.device_ip,
            }
            if settings.daylight_dimming_enabled:
                schedule = build_daylight_schedule(
                    dt=_dt.date.today(),
                    timezone=settings.daylight_dimming_timezone,
                    dawn_ramp_minutes=settings.dawn_ramp_minutes,
                    dawn_ramp_steps=settings.dawn_ramp_steps,
                    dusk_ramp_minutes=settings.dusk_ramp_minutes,
                    dusk_ramp_steps=settings.dusk_ramp_steps,
                )
                resp["computed_schedule"] = [e.model_dump() for e in schedule]
            return (200, "application/json", json.dumps(resp))

        if path == f"{PREFIX}/api/dimming/set":
            try:
                status, resp = _handle_dimming_set(query)
                return (
                    status,
                    "application/json",
                    json.dumps(resp),
                )
            except Exception as e:
                return (
                    400,
                    "application/json",
                    json.dumps({"error": str(e)}),
                )

        if path == f"{PREFIX}/api/profiles":
            return (
                200,
                "application/json",
                json.dumps(_handle_profiles_list()),
            )

        if path == f"{PREFIX}/api/profile/activate":
            status, resp = _handle_profile_activate(query)
            return (status, "application/json", json.dumps(resp))

        if path == f"{PREFIX}/api/config/stops":
            return (
                200,
                "application/json",
                json.dumps(_handle_config_stops_get()),
            )

        if path == f"{PREFIX}/api/config/settings":
            return (
                200,
                "application/json",
                json.dumps(_handle_config_settings_get()),
            )

        if path == f"{PREFIX}/dashboard":
            return (200, "text/html", generate_dashboard_html())
        if path == f"{PREFIX}/monitor":
            return (200, "text/html", generate_monitor_html())
        if path == f"{PREFIX}/simulator":
            return (200, "text/html", generate_simulator_html())

        return (404, "text/html", "<h1>404 Not Found</h1>")

    async def _serve_async(path: str, query: dict) -> tuple:
        """Handle async API endpoints. Returns (status, content_type, body) or None."""
        if path == f"{PREFIX}/api/geocode":
            status, resp = await _handle_geocode(query)
            return (status, "application/json", json.dumps(resp))
        if path == f"{PREFIX}/api/routes":
            status, resp = await _handle_routes_for_location(query)
            return (status, "application/json", json.dumps(resp))
        if path == f"{PREFIX}/api/arrivals":
            status, resp = await _handle_arrivals(query)
            return (status, "application/json", json.dumps(resp))
        # Handle /api/routes/<route_id>/stops
        if path.startswith(route_pattern_prefix) and path.endswith(
            "/stops"
        ):
            route_id = path[
                len(route_pattern_prefix) : -len("/stops")
            ]
            if route_id:
                status, resp = await _handle_stops_for_route(route_id)
                return (status, "application/json", json.dumps(resp))
        return None

    async def _handle_post(path: str, body: bytes) -> tuple:
        """Handle POST requests. Returns (status, content_type, body_str)."""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            return (
                400,
                "application/json",
                json.dumps({"error": f"Invalid JSON: {e}"}),
            )

        if path == f"{PREFIX}/api/dimming":
            from ..config import (
                load_service_settings,
                save_service_settings,
            )

            settings = load_service_settings()
            updates = {}
            for field, typ in _DAYLIGHT_FIELDS.items():
                if field in data:
                    updates[field] = typ(data[field])
            if "device_ip" in data:
                updates["device_ip"] = data["device_ip"]
            if "display_brightness" in data:
                updates["display_brightness"] = int(
                    data["display_brightness"]
                )
            if updates:
                from ..config import ServiceSettings
                settings = ServiceSettings.model_validate(
                    {**settings.model_dump(), **updates}
                )
            try:
                save_service_settings(settings)
            except Exception as e:
                return (
                    400,
                    "application/json",
                    json.dumps({"error": f"Save failed: {e}"}),
                )
            return (
                200,
                "application/json",
                json.dumps(
                    {
                        "status": "ok",
                        "daylight_dimming_enabled": settings.daylight_dimming_enabled,
                        "message": "Dimming settings saved. Takes effect within 60s.",
                    }
                ),
            )

        if path == f"{PREFIX}/api/config/stops":
            status, resp = _handle_config_stops_post(data)
            return (status, "application/json", json.dumps(resp))

        if path == f"{PREFIX}/api/config/save":
            status, resp = _handle_config_save(data)
            return (status, "application/json", json.dumps(resp))

        if path == f"{PREFIX}/api/config/settings":
            status, resp = _handle_config_settings_patch(data)
            return (status, "application/json", json.dumps(resp))

        return (
            404,
            "application/json",
            json.dumps({"error": "Not found"}),
        )

    async def _handle_delete(path: str, body: bytes) -> tuple:
        """Handle DELETE requests. Returns (status, content_type, body_str)."""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError as e:
            return (
                400,
                "application/json",
                json.dumps({"error": f"Invalid JSON: {e}"}),
            )

        if path == f"{PREFIX}/api/config/stops":
            status, resp = _handle_config_stops_delete(data)
            return (status, "application/json", json.dumps(resp))

        return (
            404,
            "application/json",
            json.dumps({"error": "Not found"}),
        )

    def handle_http(path: str, query: dict) -> tuple:
        """Return (status, headers_list, body_bytes) for an HTTP GET request."""
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
            ct = (
                "application/json"
                if "/api/" in clean
                else "text/html"
            )
            headers.append(("Content-Type", f"{ct}; charset=utf-8"))
            return (200, headers, content.encode("utf-8"))

        headers.append(("Content-Type", "text/html; charset=utf-8"))
        return (404, headers, b"<h1>404 Not Found</h1>")

    # -- WebSocket proxy: relay /ws connections to the internal WS server --
    async def ws_proxy_handler(ws):
        """Proxy a WebSocket connection to the internal server on :8000."""
        try:
            async with websockets.connect(
                "ws://localhost:8000"
            ) as upstream:

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
            log.debug(
                "WS proxy error: %s", e, extra={"component": "web"}
            )

    # -- process_request: HTTP responses for non-WS requests --
    async def process_request(connection, request):
        """Handle HTTP requests; return None for WS upgrade."""
        path = request.path
        parsed = urlparse(path)
        clean = parsed.path.rstrip("/") or "/"

        # Allow WebSocket upgrade on /transit-tracker/ws
        if clean == f"{PREFIX}/ws":
            return None

        query = parse_qs(parsed.query)
        method = getattr(request, "method", "GET") or "GET"
        headers = [("Access-Control-Allow-Origin", "*")]

        if method == "OPTIONS":
            headers.extend(
                [
                    (
                        "Access-Control-Allow-Methods",
                        "GET, POST, DELETE, PATCH, OPTIONS",
                    ),
                    ("Access-Control-Allow-Headers", "Content-Type"),
                ]
            )
            return websockets.http11.Response(
                204,
                "",
                websockets.datastructures.Headers(headers),
                b"",
            )

        if method in ("POST", "DELETE", "PATCH"):
            body_bytes = getattr(request, "body", b"") or b""
            if method == "DELETE":
                status, ct, body_str = await _handle_delete(
                    clean, body_bytes
                )
            else:
                status, ct, body_str = await _handle_post(
                    clean, body_bytes
                )
            headers.append(
                ("Content-Type", f"{ct}; charset=utf-8")
            )
            return websockets.http11.Response(
                status,
                "",
                websockets.datastructures.Headers(headers),
                body_str.encode("utf-8"),
            )

        # GET: try async endpoints first, then sync
        async_result = await _serve_async(clean, query)
        if async_result is not None:
            status, ct, body_str = async_result
            headers.append(
                ("Content-Type", f"{ct}; charset=utf-8")
            )
            return websockets.http11.Response(
                status,
                "",
                websockets.datastructures.Headers(headers),
                body_str.encode("utf-8"),
            )

        status, h_list, body = handle_http(clean, query)
        return websockets.http11.Response(
            status,
            "",
            websockets.datastructures.Headers(h_list),
            body,
        )

    log.info(
        "Transit Tracker web server at http://%s:%d%s",
        host,
        port,
        PREFIX,
        extra={"component": "web"},
    )
    log.info(
        "  %s/ws         — WebSocket relay",
        PREFIX,
        extra={"component": "web"},
    )
    log.info(
        "  %s/dashboard  — observability dashboard",
        PREFIX,
        extra={"component": "web"},
    )
    log.info(
        "  %s/simulator  — LED matrix simulator",
        PREFIX,
        extra={"component": "web"},
    )
    log.info(
        "  %s/spec       — API documentation page",
        PREFIX,
        extra={"component": "web"},
    )

    async with websockets.serve(
        ws_proxy_handler,
        host,
        port,
        process_request=process_request,
    ):
        await asyncio.Future()  # Run forever
