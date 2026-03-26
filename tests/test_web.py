"""

Test suite for the Transit Tracker web module.

Tests cover:
- Stop coordinate resolution with mock API
- API spec generation (structure, config-derived examples, ferry/bus split)
- Index page generation
- HTTP handler routing (200s, 404s, content types)
- Polyline decoding contract (Google encoded polylines)
"""

import json
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest

from transit_tracker.config import TransitConfig
from transit_tracker.transit_api import TransitAPI
from transit_tracker.web import (
    PREFIX,
    TransitWebHandler,
    _handle_config_save,
    _handle_config_settings_get,
    _handle_config_settings_patch,
    _handle_config_stops_delete,
    _handle_config_stops_get,
    _handle_config_stops_post,
    _handle_dimming_set,
    _handle_profile_activate,
    _handle_profiles_list,
    _reset_draft,
    generate_api_spec,
    generate_dashboard_html,
    generate_index_html,
    generate_monitor_html,
    generate_simulator_html,
    resolve_stop_coordinates,
)

pytestmark = pytest.mark.unit

# --- Fixtures ---


@pytest.fixture
def mock_config():
    """Config with two stops and routes."""
    return TransitConfig(
        transit_tracker={
            "base_url": "wss://tt.horner.tj/",
            "stops": [
                {
                    "stop_id": "st:1_8494",
                    "label": "Issaquah TC",
                    "time_offset": "-7min",
                    "routes": ["st:40_100240"],
                },
                {
                    "stop_id": "st:1_1920",
                    "label": "Mercer Island",
                    "time_offset": "-5min",
                    "routes": ["st:40_100240", "st:1_100039"],
                },
            ],
        }
    )


@pytest.fixture
def mock_config_with_ferry():
    """Config with bus and ferry stops."""
    return TransitConfig(
        transit_tracker={
            "base_url": "wss://tt.horner.tj/",
            "stops": [
                {
                    "stop_id": "st:1_8494",
                    "label": "554 - Issaquah TC",
                    "time_offset": "-7min",
                    "routes": ["st:40_100240"],
                },
                {
                    "stop_id": "wsf:7",
                    "label": "SEA-BI - Seattle Terminal",
                    "time_offset": "0min",
                    "routes": ["wsf:73"],
                },
            ],
        }
    )


@pytest.fixture
def sample_stops():
    """Resolved stop coordinate data."""
    return [
        {
            "stop_id": "st:1_8494",
            "name": "Issaquah Transit Center",
            "lat": 47.5301,
            "lon": -122.0326,
            "label": "Issaquah TC",
        },
        {
            "stop_id": "st:1_1920",
            "name": "Mercer Island P&R",
            "lat": 47.5707,
            "lon": -122.2220,
            "label": "Mercer Island",
        },
    ]


# --- Stop Coordinate Resolution ---


@pytest.mark.asyncio
async def test_resolve_stop_coordinates(mock_config):
    """Resolves lat/lon for all configured stops via OBA API."""
    mock_api = AsyncMock(spec=TransitAPI)
    mock_api.get_stop = AsyncMock(
        side_effect=[
            {"id": "st:1_8494", "name": "Issaquah TC", "lat": 47.53, "lon": -122.03},
            {
                "id": "st:1_1920",
                "name": "Mercer Island P&R",
                "lat": 47.57,
                "lon": -122.22,
            },
        ]
    )
    mock_api.close = AsyncMock()

    with patch("transit_tracker.web.api_handlers.TransitAPI", return_value=mock_api):
        stops = await resolve_stop_coordinates(mock_config)

    assert len(stops) == 2
    assert stops[0]["stop_id"] == "st:1_8494"
    assert stops[0]["lat"] == 47.53
    assert stops[0]["label"] == "Issaquah TC"
    assert stops[1]["stop_id"] == "st:1_1920"
    assert stops[1]["label"] == "Mercer Island"
    mock_api.close.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_stop_coordinates_handles_api_errors(mock_config):
    """Gracefully skips stops that fail API lookup."""
    mock_api = AsyncMock(spec=TransitAPI)
    mock_api.get_stop = AsyncMock(
        side_effect=[
            Exception("Network error"),
            {
                "id": "st:1_1920",
                "name": "Mercer Island P&R",
                "lat": 47.57,
                "lon": -122.22,
            },
        ]
    )
    mock_api.close = AsyncMock()

    with patch("transit_tracker.web.api_handlers.TransitAPI", return_value=mock_api):
        stops = await resolve_stop_coordinates(mock_config)

    assert len(stops) == 1
    assert stops[0]["stop_id"] == "st:1_1920"


@pytest.mark.asyncio
async def test_resolve_stop_coordinates_handles_not_found(mock_config):
    """Gracefully skips stops that return None."""
    mock_api = AsyncMock(spec=TransitAPI)
    mock_api.get_stop = AsyncMock(side_effect=[None, None])
    mock_api.close = AsyncMock()

    with patch("transit_tracker.web.api_handlers.TransitAPI", return_value=mock_api):
        stops = await resolve_stop_coordinates(mock_config)

    assert len(stops) == 0


# --- Polyline Decoding Contract ---


def test_polyline_decode_basic():
    """Google polyline encoding contract: known encoded string -> known coords."""
    result = TransitAPI._decode_polyline("_p~iF~ps|U")
    assert len(result) == 1
    assert abs(result[0][1] - 38.5) < 0.001
    assert abs(result[0][0] - (-120.2)) < 0.001


def test_polyline_decode_multi_point():
    """Multi-point polyline decodes to sequential coordinates."""
    result = TransitAPI._decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert len(result) == 3
    assert abs(result[0][1] - 38.5) < 0.001
    assert result[0] != result[1]


def test_polyline_decode_empty():
    """Empty string returns empty list."""
    assert TransitAPI._decode_polyline("") == []


# --- API Spec Generation ---


def test_api_spec_is_valid_json(mock_config):
    """generate_api_spec returns valid JSON."""
    spec_str = generate_api_spec(mock_config)
    spec = json.loads(spec_str)
    assert isinstance(spec, dict)


def test_api_spec_has_info(mock_config):
    """Spec includes info section with title and version."""
    spec = json.loads(generate_api_spec(mock_config))
    assert spec["info"]["title"] == "Transit Tracker WebSocket API"
    assert spec["info"]["version"] == "1.0.0"
    assert "websocket_url" in spec["info"]


def test_api_spec_has_config(mock_config):
    """Spec includes config section derived from live config."""
    spec = json.loads(generate_api_spec(mock_config))
    assert "subscriptions" in spec["config"]
    assert len(spec["config"]["subscriptions"]) > 0
    sub = spec["config"]["subscriptions"][0]
    assert "route" in sub
    assert "stop" in sub


def test_api_spec_has_messages(mock_config):
    """Spec documents client_to_server and server_to_client messages."""
    spec = json.loads(generate_api_spec(mock_config))
    assert "schedule:subscribe" in spec["messages"]["client_to_server"]
    assert "schedule" in spec["messages"]["server_to_client"]
    assert "heartbeat" in spec["messages"]["server_to_client"]


def test_api_spec_has_subscribe_example(mock_config):
    """Subscribe message includes a working example with routeStopPairs."""
    spec = json.loads(generate_api_spec(mock_config))
    example = spec["messages"]["client_to_server"]["schedule:subscribe"]["example"]
    assert example["event"] == "schedule:subscribe"
    assert "routeStopPairs" in example["data"]
    # Pairs should contain route,stop format
    pairs = example["data"]["routeStopPairs"]
    assert "," in pairs


def test_api_spec_has_trip_type(mock_config):
    """Spec documents Trip type with all fields."""
    spec = json.loads(generate_api_spec(mock_config))
    trip_type = spec["types"]["Trip"]
    for field in [
        "tripId",
        "routeId",
        "routeName",
        "routeColor",
        "stopId",
        "headsign",
        "arrivalTime",
        "departureTime",
        "isRealtime",
    ]:
        assert field in trip_type


def test_api_spec_ferry_bus_examples(mock_config_with_ferry):
    """Spec generates separate bus and ferry examples from config."""
    spec = json.loads(generate_api_spec(mock_config_with_ferry))
    schedule = spec["messages"]["server_to_client"]["schedule"]
    assert "bus" in schedule["examples"]
    assert "ferry" in schedule["examples"]


def test_api_spec_has_id_prefixes(mock_config):
    """Spec documents st: and wsf: ID prefixes."""
    spec = json.loads(generate_api_spec(mock_config))
    assert "st:" in spec["id_prefixes"]
    assert "wsf:" in spec["id_prefixes"]


def test_api_spec_has_rate_limiting(mock_config):
    """Spec documents rate limiting behavior."""
    spec = json.loads(generate_api_spec(mock_config))
    assert "backoff" in spec["rate_limiting"]
    assert "per_stop_cooldown" in spec["rate_limiting"]


# --- Index Page ---


def test_index_html_lists_pages():
    """Index page contains links to all registered pages."""
    pages = [
        {"path": "/api/spec", "name": "API Spec", "description": "API docs"},
        {"path": "/api/stops", "name": "Stops", "description": "Stop data"},
    ]
    html = generate_index_html(pages)
    assert "/api/spec" in html
    assert "API Spec" in html
    assert "/api/stops" in html
    assert "Stops" in html


def test_index_html_has_transit_tracker_title():
    """Index page has the Transit Tracker heading."""
    html = generate_index_html([])
    assert "Transit Tracker" in html


# --- HTTP Handler ---


def test_handler_serves_registered_routes():
    """Handler returns 200 for registered routes."""
    TransitWebHandler.routes = {
        f"{PREFIX}": "<html>index</html>",
        f"{PREFIX}/api/spec": '{"info": {}}',
        f"{PREFIX}/api/stops": "[]",
    }

    handler = _make_handler("GET", f"{PREFIX}/api/spec")
    assert handler._status_code == 200
    assert b'{"info": {}}' in handler._body


def test_handler_returns_404_for_unknown():
    """Handler returns 404 for unregistered paths."""
    TransitWebHandler.routes = {f"{PREFIX}": "index"}

    handler = _make_handler("GET", "/nonexistent")
    assert handler._status_code == 404


def test_handler_json_content_type_for_api():
    """API routes get application/json content type."""
    TransitWebHandler.routes = {f"{PREFIX}/api/stops": "[]"}

    handler = _make_handler("GET", f"{PREFIX}/api/stops")
    assert handler._status_code == 200
    assert "application/json" in handler._content_type


def test_handler_html_content_type_for_pages():
    """Page routes get text/html content type."""
    TransitWebHandler.routes = {f"{PREFIX}": "<html></html>"}

    handler = _make_handler("GET", f"{PREFIX}")
    assert handler._status_code == 200
    assert "text/html" in handler._content_type


def test_handler_strips_trailing_slash():
    """Trailing slashes are normalized."""
    TransitWebHandler.routes = {f"{PREFIX}/api/spec": "{}"}

    handler = _make_handler("GET", f"{PREFIX}/api/spec/")
    assert handler._status_code == 200


def test_handler_strips_query_string():
    """Query strings are stripped from path matching."""
    TransitWebHandler.routes = {f"{PREFIX}/api/spec": "{}"}

    handler = _make_handler("GET", f"{PREFIX}/api/spec?format=pretty")
    assert handler._status_code == 200


def test_handler_cors_header():
    """Responses include CORS header for cross-origin access."""
    TransitWebHandler.routes = {f"{PREFIX}/api/stops": "[]"}

    handler = _make_handler("GET", f"{PREFIX}/api/stops")
    assert handler._cors_header == "*"


# --- Helper to create mock HTTP handler ---


def _make_handler(method, path):
    """Create a mock TransitWebHandler and execute a request."""

    class MockHandler(TransitWebHandler):
        _status_code = None
        _body = b""
        _content_type = ""
        _cors_header = ""

        def __init__(self):
            self.path = path
            self.wfile = BytesIO()
            self._headers = {}

        def send_response(self, code):
            self._status_code = code

        def send_header(self, key, value):
            self._headers[key] = value
            if key == "Content-Type":
                self._content_type = value
            if key == "Access-Control-Allow-Origin":
                self._cors_header = value

        def end_headers(self):
            pass

        def log_message(self, format, *args):
            pass

    handler = MockHandler()
    handler.do_GET()
    handler._body = handler.wfile.getvalue()
    return handler


# --- Simulator HTML generation ---


def test_simulator_html_has_canvas():
    html = generate_simulator_html()
    assert "<canvas" in html
    assert "led-canvas" in html


def test_simulator_html_has_glyphs():
    html = generate_simulator_html()
    assert "GLYPHS" in html
    assert "REALTIME_ICON" in html


def test_simulator_html_has_subscribe_payload():
    html = generate_simulator_html()
    assert "SUBSCRIBE_PAYLOAD" in html
    assert "schedule:subscribe" in html


def test_simulator_html_has_ws_proxy_url():
    """Simulator should connect via /ws proxy, not direct :8000."""
    html = generate_simulator_html()
    assert "/ws" in html
    assert "getWsUrl" in html


def test_simulator_html_protocol_detection():
    """Simulator should auto-detect wss:// vs ws:// based on page protocol."""
    html = generate_simulator_html()
    assert "location.protocol" in html
    assert "wss://" in html


def test_simulator_html_endpoint_selector():
    html = generate_simulator_html()
    assert "endpoint-select" in html
    assert "Local" in html
    assert "Cloud" in html
    assert "Custom" in html


# --- Monitor HTML generation ---


def test_monitor_html_has_topology():
    html = generate_monitor_html()
    assert "topo-svg" in html
    assert "Network Topology" in html


def test_monitor_html_has_simulator_toggle():
    html = generate_monitor_html()
    assert "sim-toggle" in html
    assert "LED Simulator" in html
    assert "sim-iframe" in html


def test_monitor_html_has_message_feed():
    html = generate_monitor_html()
    assert "feed-list" in html
    assert "Message Flow" in html


# --- Dashboard HTML generation ---


def test_dashboard_html_has_metrics():
    html = generate_dashboard_html()
    assert "fetchMetrics" in html


def test_dashboard_html_has_charts():
    html = generate_dashboard_html()
    assert "chart" in html.lower() or "metric" in html.lower()
    assert "api_calls" in html or "apiCalls" in html


def test_monitor_html_has_trip_table():
    html = generate_monitor_html()
    assert "trip-table" in html
    assert "Route" in html
    assert "Headsign" in html


def test_monitor_html_has_status_polling():
    html = generate_monitor_html()
    assert "/transit-tracker/api/status" in html
    assert "setInterval" in html


def test_simulator_html_has_pixel_rendering():
    html = generate_simulator_html()
    assert "PIXEL_SCALE" in html
    assert "requestAnimationFrame" in html
    assert "renderFrame" in html


def test_simulator_html_has_trip_processing():
    """Simulator JS should have client-side trip processing."""
    html = generate_simulator_html()
    assert "processDepartures" in html
    assert "diversity" in html.lower() or "stop_id" in html


# --- Profile API helpers ---


def test_handle_profiles_list():
    """profiles list returns profiles with active flag."""
    p1 = "transit_tracker.config.get_last_config_path"
    p2 = "transit_tracker.config.list_profiles"
    with (
        patch(p1, return_value="/a/b.yaml"),
        patch(p2, return_value=["/a/b.yaml", "/a/c.yaml"]),
    ):
        result = _handle_profiles_list()
    assert len(result["profiles"]) == 2
    assert result["profiles"][0]["active"] is True
    assert result["profiles"][1]["active"] is False
    assert result["active"] == "/a/b.yaml"


def test_handle_profile_activate_missing_name():
    """activate without name returns 400."""
    status, body = _handle_profile_activate({})
    assert status == 400
    assert "error" in body


def test_handle_profile_activate_not_found():
    """activate with unknown name returns 404."""
    with patch("transit_tracker.config.list_profiles", return_value=["/a/b.yaml"]):
        status, body = _handle_profile_activate({"name": ["nope.yaml"]})
    assert status == 404
    assert "available" in body


def test_handle_profile_activate_success():
    """activate with valid name returns 200."""
    with (
        patch("transit_tracker.config.list_profiles", return_value=["/a/b.yaml"]),
        patch("transit_tracker.config.set_last_config_path") as mock_set,
    ):
        status, body = _handle_profile_activate({"name": ["b.yaml"]})
    assert status == 200
    assert body["profile"] == "b.yaml"
    mock_set.assert_called_once_with("/a/b.yaml")


# --- Dimming set helper ---


def test_handle_dimming_set_brightness():
    """dimming set updates brightness."""
    mock_settings = patch("transit_tracker.config.load_service_settings")
    mock_save = patch("transit_tracker.config.save_service_settings")
    with mock_settings as m_load, mock_save as m_save:
        from unittest.mock import MagicMock

        settings = MagicMock()
        settings.daylight_dimming_enabled = False
        settings.display_brightness = 100
        settings.device_ip = None
        m_load.return_value = settings
        status, body = _handle_dimming_set({"brightness": ["50"]})
    assert status == 200
    assert settings.display_brightness == 50
    m_save.assert_called_once()


# --- Draft config (stop CRUD) helpers ---


def test_handle_config_stops_get(mock_config):
    """config stops get returns stops from draft."""
    import transit_tracker.web.api_handlers as web_mod

    web_mod._draft_config = mock_config
    web_mod._draft_dirty = False
    result = _handle_config_stops_get()
    assert len(result["stops"]) == 2
    assert result["stops"][0]["stop_id"] == "st:1_8494"
    assert result["dirty"] is False
    web_mod._draft_config = None


def test_handle_config_stops_post(mock_config):
    """config stops post adds a stop to draft."""
    import transit_tracker.web.api_handlers as web_mod

    web_mod._draft_config = mock_config
    web_mod._draft_dirty = False
    status, body = _handle_config_stops_post(
        {
            "stop_id": "st:1_9999",
            "routes": ["st:40_100"],
            "label": "Test Stop",
        }
    )
    assert status == 200
    assert body["stop"]["stop_id"] == "st:1_9999"
    assert web_mod._draft_dirty is True
    assert len(mock_config.transit_tracker.stops) == 3
    web_mod._draft_config = None


def test_handle_config_stops_post_missing_id():
    """config stops post without stop_id returns 400."""
    import transit_tracker.web.api_handlers as web_mod

    web_mod._draft_config = TransitConfig(transit_tracker={"stops": []})
    status, body = _handle_config_stops_post({})
    assert status == 400
    web_mod._draft_config = None


def test_handle_config_stops_delete_by_index(mock_config):
    """config stops delete by index removes correct stop."""
    import transit_tracker.web.api_handlers as web_mod

    web_mod._draft_config = mock_config
    web_mod._draft_dirty = False
    original_count = len(mock_config.transit_tracker.stops)
    status, body = _handle_config_stops_delete({"index": 0})
    assert status == 200
    assert len(mock_config.transit_tracker.stops) == original_count - 1
    assert web_mod._draft_dirty is True
    web_mod._draft_config = None


def test_handle_config_stops_delete_by_stop_id(mock_config):
    """config stops delete by stop_id removes correct stop."""
    import transit_tracker.web.api_handlers as web_mod

    web_mod._draft_config = mock_config
    web_mod._draft_dirty = False
    status, body = _handle_config_stops_delete({"stop_id": "st:1_1920"})
    assert status == 200
    assert body["removed"]["stop_id"] == "st:1_1920"
    web_mod._draft_config = None


def test_handle_config_stops_delete_not_found():
    """config stops delete with unknown stop_id returns 404."""
    import transit_tracker.web.api_handlers as web_mod

    web_mod._draft_config = TransitConfig(transit_tracker={"stops": []})
    status, body = _handle_config_stops_delete({"stop_id": "nope"})
    assert status == 404
    web_mod._draft_config = None


def test_handle_config_stops_delete_no_params():
    """config stops delete without params returns 400."""
    import transit_tracker.web.api_handlers as web_mod

    web_mod._draft_config = TransitConfig(transit_tracker={"stops": []})
    status, body = _handle_config_stops_delete({})
    assert status == 400
    web_mod._draft_config = None


# --- Config save helper ---


def test_handle_config_save_with_path():
    """config save writes draft to specified path."""
    from unittest.mock import MagicMock

    import transit_tracker.web.api_handlers as web_mod

    mock_draft = MagicMock()
    web_mod._draft_config = mock_draft
    web_mod._draft_dirty = True
    status, body = _handle_config_save({"path": "/tmp/test.yaml"})
    assert status == 200
    mock_draft.save.assert_called_once_with("/tmp/test.yaml")
    assert web_mod._draft_dirty is False
    web_mod._draft_config = None


def test_handle_config_save_no_path_no_active():
    """config save without path and no active profile returns 400."""
    import transit_tracker.web.api_handlers as web_mod

    web_mod._draft_config = TransitConfig(transit_tracker={"stops": []})
    web_mod._draft_dirty = True
    with patch("transit_tracker.config.get_last_config_path", return_value=None):
        status, body = _handle_config_save({})
    assert status == 400
    web_mod._draft_config = None


# --- Config settings helpers ---


def test_handle_config_settings_get():
    """config settings get returns service settings."""
    from unittest.mock import MagicMock

    load = "transit_tracker.config.load_service_settings"
    mock_settings = MagicMock()
    mock_settings.model_dump.return_value = {
        "check_interval_seconds": 30,
    }
    with patch(load, return_value=mock_settings):
        result = _handle_config_settings_get()
    assert result == {"check_interval_seconds": 30}


def test_handle_config_settings_patch_valid():
    """config settings patch updates allowed fields."""
    from unittest.mock import MagicMock

    load = "transit_tracker.config.load_service_settings"
    save = "transit_tracker.config.save_service_settings"
    mock_settings = MagicMock()
    mock_settings.check_interval_seconds = 30
    with patch(load, return_value=mock_settings), patch(save) as mock_save:
        status, body = _handle_config_settings_patch(
            {"check_interval_seconds": 60},
        )
    assert status == 200
    assert "check_interval_seconds" in body["updated"]
    mock_save.assert_called_once()


def test_handle_config_settings_patch_invalid():
    """config settings patch with no valid fields returns 400."""
    from unittest.mock import MagicMock

    load = "transit_tracker.config.load_service_settings"
    mock_settings = MagicMock()
    with patch(load, return_value=mock_settings):
        status, body = _handle_config_settings_patch(
            {"oba_api_key": "secret"},
        )
    assert status == 400
    assert "allowed" in body


# --- Draft reset ---


def test_reset_draft():
    """reset_draft clears the cached draft."""
    import transit_tracker.web.api_handlers as web_mod

    web_mod._draft_config = "something"
    web_mod._draft_dirty = True
    _reset_draft()
    assert web_mod._draft_config is None
    assert web_mod._draft_dirty is False
