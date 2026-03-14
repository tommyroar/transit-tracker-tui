"""
Comprehensive test suite for the Transit Tracker web module.

Tests cover:
- Mapbox token resolution (env, config, missing)
- Stop coordinate resolution with mock API
- Route polyline resolution and deduplication
- Walkshed HTML generation (structure, data injection, Mapbox integration)
- Simulator HTML generation (MicroFont port, WebSocket config, Canvas setup)
- Index page generation
- HTTP handler routing (200s, 404s, content types)
- Polyline decoding contract (Google encoded polylines)
- Simulator-web equivalence (JS glyph data matches Python source)
"""

import os
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest

from transit_tracker.config import TransitConfig
from transit_tracker.transit_api import TransitAPI
from transit_tracker.web import (
    TransitWebHandler,
    generate_index_html,
    generate_simulator_html,
    generate_walkshed_html,
    get_mapbox_token,
    resolve_route_polylines,
    resolve_stop_coordinates,
)

# --- Fixtures ---


@pytest.fixture
def mock_config():
    """Config with two stops and routes."""
    return TransitConfig(
        transit_tracker={
            "base_url": "wss://tt.horner.tj/",
            "mapbox_access_token": "pk.test_token_from_config",
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
def mock_config_no_token():
    """Config with no mapbox token."""
    return TransitConfig(
        transit_tracker={
            "stops": [
                {"stop_id": "st:1_8494", "routes": ["st:40_100240"]},
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


@pytest.fixture
def sample_routes():
    """Resolved route polyline data."""
    return [
        {
            "route_id": "st:40_100240",
            "name": "554",
            "color": "2B376E",
            "polylines": [[[-122.03, 47.53], [-122.10, 47.55], [-122.22, 47.57]]],
        }
    ]


# --- Token Resolution ---


def test_mapbox_token_from_env(mock_config):
    """Token from env var takes priority over config."""
    with patch.dict(os.environ, {"MAPBOX_ACCESS_TOKEN": "pk.env_token"}):
        assert get_mapbox_token(mock_config) == "pk.env_token"


def test_mapbox_token_from_config(mock_config):
    """Falls back to config when env var is not set."""
    with patch.dict(os.environ, {}, clear=True):
        env = os.environ.copy()
        env.pop("MAPBOX_ACCESS_TOKEN", None)
        with patch.dict(os.environ, env, clear=True):
            assert get_mapbox_token(mock_config) == "pk.test_token_from_config"


def test_mapbox_token_missing(mock_config_no_token):
    """Raises RuntimeError with clear message when no token available."""
    with patch.dict(os.environ, {}, clear=True):
        env = os.environ.copy()
        env.pop("MAPBOX_ACCESS_TOKEN", None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="Mapbox access token not found"):
                get_mapbox_token(mock_config_no_token)


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

    with patch("transit_tracker.web.TransitAPI", return_value=mock_api):
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

    with patch("transit_tracker.web.TransitAPI", return_value=mock_api):
        stops = await resolve_stop_coordinates(mock_config)

    assert len(stops) == 1
    assert stops[0]["stop_id"] == "st:1_1920"


@pytest.mark.asyncio
async def test_resolve_stop_coordinates_handles_not_found(mock_config):
    """Gracefully skips stops that return None."""
    mock_api = AsyncMock(spec=TransitAPI)
    mock_api.get_stop = AsyncMock(side_effect=[None, None])
    mock_api.close = AsyncMock()

    with patch("transit_tracker.web.TransitAPI", return_value=mock_api):
        stops = await resolve_stop_coordinates(mock_config)

    assert len(stops) == 0


# --- Route Polyline Resolution ---


@pytest.mark.asyncio
async def test_resolve_route_polylines_deduplicates(mock_config):
    """Routes shared across stops are only fetched once."""
    mock_api = AsyncMock(spec=TransitAPI)
    mock_api.get_route_polylines = AsyncMock(
        return_value={
            "route_id": "st:40_100240",
            "name": "554",
            "color": "2B376E",
            "polylines": [[[-122.03, 47.53]]],
        }
    )
    mock_api.close = AsyncMock()

    with patch("transit_tracker.web.TransitAPI", return_value=mock_api):
        routes = await resolve_route_polylines(mock_config)

    # st:40_100240 appears in both stops but should only be fetched
    # once (2 unique routes total: st:40_100240 and st:1_100039)
    assert mock_api.get_route_polylines.call_count == 2
    # But only the one with polylines should be in results
    assert len(routes) >= 1


@pytest.mark.asyncio
async def test_resolve_route_polylines_skips_empty():
    """Routes with no polyline data are excluded."""
    config = TransitConfig(
        transit_tracker={
            "stops": [
                {"stop_id": "st:1_1", "routes": ["st:empty_route"]},
            ]
        }
    )
    mock_api = AsyncMock(spec=TransitAPI)
    mock_api.get_route_polylines = AsyncMock(
        return_value={
            "route_id": "st:empty_route",
            "name": "",
            "color": "",
            "polylines": [],
        }
    )
    mock_api.close = AsyncMock()

    with patch("transit_tracker.web.TransitAPI", return_value=mock_api):
        routes = await resolve_route_polylines(config)

    assert len(routes) == 0


# --- Polyline Decoding Contract ---


def test_polyline_decode_basic():
    """Google polyline encoding contract: known encoded string -> known coords."""
    # "_p~iF~ps|U" encodes to (38.5, -120.2) in the standard format
    result = TransitAPI._decode_polyline("_p~iF~ps|U")
    assert len(result) == 1
    assert abs(result[0][1] - 38.5) < 0.001  # lat
    assert abs(result[0][0] - (-120.2)) < 0.001  # lng


def test_polyline_decode_multi_point():
    """Multi-point polyline decodes to sequential coordinates."""
    # "_p~iF~ps|U_ulLnnqC_mqNvxq`@" encodes 3 points
    result = TransitAPI._decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert len(result) == 3
    # First point: (38.5, -120.2)
    assert abs(result[0][1] - 38.5) < 0.001
    # Points should be different (delta encoding)
    assert result[0] != result[1]


def test_polyline_decode_empty():
    """Empty string returns empty list."""
    assert TransitAPI._decode_polyline("") == []


# --- Walkshed HTML Generation ---


def test_walkshed_html_embeds_stops(sample_stops):
    """Stop data is properly embedded as JSON in the HTML."""
    html = generate_walkshed_html(sample_stops, "pk.test")
    # Verify stops JSON is embedded
    assert '"stop_id": "st:1_8494"' in html
    assert '"stop_id": "st:1_1920"' in html
    assert '"lat": 47.5301' in html
    assert '"lon": -122.0326' in html


def test_walkshed_html_embeds_token(sample_stops):
    """Mapbox token is properly embedded in the HTML."""
    html = generate_walkshed_html(sample_stops, "pk.my_secret_token")
    assert "pk.my_secret_token" in html


def test_walkshed_html_includes_mapbox_gl(sample_stops):
    """Mapbox GL JS library is loaded from CDN."""
    html = generate_walkshed_html(sample_stops, "pk.test")
    assert "api.mapbox.com/mapbox-gl-js" in html
    assert "mapbox-gl.css" in html


def test_walkshed_html_uses_light_style(sample_stops):
    """Uses the light-v11 base style for architectural look (Issue #16)."""
    html = generate_walkshed_html(sample_stops, "pk.test")
    assert "light-v11" in html


def test_walkshed_html_has_isochrone_api(sample_stops):
    """Client-side JS calls Mapbox Isochrone API."""
    html = generate_walkshed_html(sample_stops, "pk.test")
    assert "isochrone/v1/mapbox/walking" in html
    assert "contours_minutes=5,10,15" in html


def test_walkshed_html_architectural_styling(sample_stops):
    """Issue #16: Architectural styling is applied."""
    html = generate_walkshed_html(sample_stops, "pk.test")
    # Building styling
    assert "applyArchitecturalStyle" in html
    # Color palette
    assert "#d1d5db" in html  # buildings
    assert "#9ca3af" in html  # building outline
    assert "#f58220" in html  # highlight orange


def test_walkshed_html_pill_labels(sample_stops):
    """Issue #16: Stop markers use pill-style labels."""
    html = generate_walkshed_html(sample_stops, "pk.test")
    assert "stop-pill" in html


def test_walkshed_html_includes_route_lines(sample_stops, sample_routes):
    """Issue #14: Route polylines are rendered on the map."""
    html = generate_walkshed_html(sample_stops, "pk.test", routes=sample_routes)
    assert "ROUTES" in html
    assert '"name": "554"' in html
    assert "LineString" in html


def test_walkshed_html_no_routes(sample_stops):
    """Walkshed page works without route data."""
    html = generate_walkshed_html(sample_stops, "pk.test", routes=None)
    assert "ROUTES = []" in html


def test_walkshed_html_legend(sample_stops):
    """Walk time legend is present."""
    html = generate_walkshed_html(sample_stops, "pk.test")
    assert "5 minutes" in html
    assert "10 minutes" in html
    assert "15 minutes" in html


# --- Simulator HTML Generation ---


def test_simulator_html_embeds_ws_config(mock_config):
    """WebSocket URL and subscription pairs are embedded."""
    html = generate_simulator_html(mock_config)
    assert "wss://tt.horner.tj/" in html or "apiUrl" in html
    # Should contain route-stop pair strings
    assert "pairsStr" in html


def test_simulator_html_has_glyphs(mock_config):
    """MicroFont GLYPHS dict is ported to JavaScript."""
    html = generate_simulator_html(mock_config)
    # Check several glyph entries match Python source
    assert "0x0E,0x11,0x11,0x11,0x11,0x11,0x0E" in html  # '0'
    assert "0x04,0x0C,0x04,0x04,0x04,0x04,0x0E" in html  # '1'
    assert "0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F" in html  # 'E'


def test_simulator_html_has_realtime_icon(mock_config):
    """REALTIME_ICON animation data is present."""
    html = generate_simulator_html(mock_config)
    assert "REALTIME_ICON" in html
    # Check the icon pattern
    assert "[0,0,0,3,3,3]" in html
    assert "[3,0,2,0,1,1]" in html


def test_simulator_html_has_canvas(mock_config):
    """HTML5 Canvas element is set up for LED rendering."""
    html = generate_simulator_html(mock_config)
    assert '<canvas id="led"' in html
    assert "getContext" in html


def test_simulator_html_has_websocket_client(mock_config):
    """WebSocket client with schedule:subscribe protocol."""
    html = generate_simulator_html(mock_config)
    assert "new WebSocket" in html
    assert "schedule:subscribe" in html
    assert "Web Simulator" in html  # client_name


def test_simulator_html_has_animation_loop(mock_config):
    """RequestAnimationFrame loop for LED rendering."""
    html = generate_simulator_html(mock_config)
    assert "requestAnimationFrame" in html


def test_simulator_html_has_scrolling(mock_config):
    """Headsign scrolling logic is present."""
    html = generate_simulator_html(mock_config)
    assert "scrollOff" in html or "scrollOffset" in html


def test_simulator_html_has_diversity_capping(mock_config):
    """Fair diversity capping logic is ported."""
    html = generate_simulator_html(mock_config)
    assert "seenStops" in html
    assert "limit" in html


def test_simulator_html_has_color_mapping(mock_config):
    """Color name to hex mapping matches Python simulator."""
    html = generate_simulator_html(mock_config)
    assert "yellow" in html
    assert "hot_pink" in html
    assert "bright_blue" in html
    assert "#FFD700" in html  # yellow hex


def test_simulator_html_connection_status(mock_config):
    """Connection status indicator is present."""
    html = generate_simulator_html(mock_config)
    assert "statusDot" in html or "connected" in html
    assert "disconnected" in html
    assert "connecting" in html


def test_simulator_html_display_dimensions(mock_config):
    """Canvas dimensions match config panel settings."""
    html = generate_simulator_html(mock_config)
    # Default: 2 panels * 64 = 128 width
    assert "displayWidth: 128" in html


# --- Simulator-Python Equivalence ---


def test_simulator_glyph_equivalence():
    """JS glyph data in HTML matches Python MicroFont.GLYPHS exactly."""
    from transit_tracker.simulator import MicroFont

    config = TransitConfig(
        transit_tracker={"stops": [{"stop_id": "st:1_1", "routes": ["st:1_1"]}]}
    )
    html = generate_simulator_html(config)

    # Verify every Python glyph appears in JS
    for char, glyph_data in MicroFont.GLYPHS.items():
        hex_str = ",".join(f"0x{b:02X}" for b in glyph_data)
        assert hex_str in html, f"Glyph '{char}' ({hex_str}) missing"


def test_simulator_realtime_icon_equivalence():
    """JS REALTIME_ICON matches Python MicroFont.REALTIME_ICON exactly."""
    from transit_tracker.simulator import MicroFont

    config = TransitConfig(
        transit_tracker={"stops": [{"stop_id": "st:1_1", "routes": ["st:1_1"]}]}
    )
    html = generate_simulator_html(config)

    for row in MicroFont.REALTIME_ICON:
        row_str = "[" + ",".join(str(v) for v in row) + "]"
        assert row_str in html, f"REALTIME_ICON row {row_str} missing"


def test_simulator_subscription_pairs(mock_config):
    """Subscription pairs string matches expected format from config."""
    html = generate_simulator_html(mock_config)
    # Config has 3 subscriptions after flattening (2 stops, one with 2 routes)
    # Verify the pairs string contains route,stop,offset format
    assert "st:40_100240" in html
    assert "st:1_8494" in html


# --- Index Page ---


def test_index_html_lists_pages():
    """Index page contains links to all registered pages."""
    pages = [
        {"path": "/walkshed", "name": "Walksheds", "description": "Walk map"},
        {"path": "/simulator", "name": "LED Simulator", "description": "LED sim"},
    ]
    html = generate_index_html(pages)
    assert "/walkshed" in html
    assert "Walksheds" in html
    assert "/simulator" in html
    assert "LED Simulator" in html


def test_index_html_has_transit_tracker_title():
    """Index page has the Transit Tracker heading."""
    html = generate_index_html([])
    assert "Transit Tracker" in html


# --- HTTP Handler ---


def test_handler_serves_registered_routes():
    """Handler returns 200 for registered routes."""
    TransitWebHandler.routes = {
        "/": "<html>index</html>",
        "/walkshed": "<html>walkshed</html>",
        "/api/stops": "[]",
    }

    handler = _make_handler("GET", "/walkshed")
    assert handler._status_code == 200
    assert b"walkshed" in handler._body


def test_handler_returns_404_for_unknown():
    """Handler returns 404 for unregistered paths."""
    TransitWebHandler.routes = {"/": "index"}

    handler = _make_handler("GET", "/nonexistent")
    assert handler._status_code == 404


def test_handler_json_content_type_for_api():
    """API routes get application/json content type."""
    TransitWebHandler.routes = {"/api/stops": "[]"}

    handler = _make_handler("GET", "/api/stops")
    assert handler._status_code == 200
    assert "application/json" in handler._content_type


def test_handler_html_content_type_for_pages():
    """Page routes get text/html content type."""
    TransitWebHandler.routes = {"/walkshed": "<html></html>"}

    handler = _make_handler("GET", "/walkshed")
    assert handler._status_code == 200
    assert "text/html" in handler._content_type


def test_handler_strips_trailing_slash():
    """Trailing slashes are normalized."""
    TransitWebHandler.routes = {"/walkshed": "<html>ok</html>"}

    handler = _make_handler("GET", "/walkshed/")
    assert handler._status_code == 200


def test_handler_strips_query_string():
    """Query strings are stripped from path matching."""
    TransitWebHandler.routes = {"/walkshed": "<html>ok</html>"}

    handler = _make_handler("GET", "/walkshed?token=abc")
    assert handler._status_code == 200


def test_handler_cors_header():
    """Responses include CORS header for cross-origin access."""
    TransitWebHandler.routes = {"/api/stops": "[]"}

    handler = _make_handler("GET", "/api/stops")
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
