"""
Test suite for the station overview map, walkshed modal, and route polylines.

Tests cover:
- Index map: Leaflet rendering, WSF stop embedding, WSDOT bounds, modal structure
- Stations GeoJSON: loading, fallback on missing file, schema validation
- Route polylines: GTFS shape resolution, agency prefix stripping, deduplication
- Walkshed HTML: Leaflet rendering, stop markers, circle geometry
- GeoJSON content type: handler serves .geojson with correct MIME type
- Build script: station deduplication, isochrone fetch, output schema
"""

import csv
import json
import os
import sqlite3
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from transit_tracker.config import TransitConfig
from transit_tracker.web import (
    TransitWebHandler,
    build_stations_geojson,
    generate_index_html,
    generate_walkshed_html,
    resolve_route_polylines,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config():
    """Config with a Sound Transit route subscription."""
    return TransitConfig(
        transit_tracker={
            "base_url": "wss://tt.horner.tj/",
            "stops": [
                {
                    "stop_id": "st:1_8494",
                    "label": "Issaquah TC",
                    "time_offset": "0min",
                    "routes": ["st:40_100479"],
                },
            ],
        }
    )


@pytest.fixture
def sample_wsf_stops():
    """Minimal WSF terminal data for index map tests."""
    return [
        {"name": "Seattle", "lat": 47.6023, "lon": -122.3384},
        {"name": "Bainbridge Island", "lat": 47.6227, "lon": -122.5105},
    ]


@pytest.fixture
def sample_stops():
    """Resolved stop coordinates for walkshed tests."""
    return [
        {"stop_id": "st:1_8494", "name": "Issaquah TC", "lat": 47.5301, "lon": -122.0326, "label": "Issaquah TC"},
        {"stop_id": "st:1_1920", "name": "Mercer Island", "lat": 47.5707, "lon": -122.2220, "label": "Mercer Island"},
    ]


@pytest.fixture
def sample_routes():
    """Route polyline data for walkshed tests."""
    return [
        {
            "name": "1 Line",
            "color": "#28813F",
            "polylines": [[[47.6, -122.3], [47.7, -122.3]]],
        },
    ]


@pytest.fixture
def gtfs_tree(tmp_path):
    """Create a minimal GTFS file tree for agency 40 (Sound Transit)."""
    agency_dir = tmp_path / "gtfs" / "40"
    agency_dir.mkdir(parents=True)

    # routes.txt
    with open(agency_dir / "routes.txt", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["agency_id", "route_id", "route_short_name", "route_long_name", "route_type", "route_color", "route_text_color"])
        w.writerow(["40", "100479", "1 Line", "Lynnwood - Federal Way", "0", "28813F", "FFFFFF"])
        w.writerow(["40", "2LINE", "2 Line", "Lynnwood - Redmond", "0", "007CAD", "FFFFFF"])

    # trips.txt
    with open(agency_dir / "trips.txt", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"])
        w.writerow(["100479", "WD", "T001", "Federal Way", "0", "S1"])
        w.writerow(["100479", "WD", "T002", "Lynnwood", "1", "S2"])
        w.writerow(["2LINE", "WD", "T003", "Redmond", "0", "S3"])

    # stop_times.txt
    with open(agency_dir / "stop_times.txt", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence"])
        w.writerow(["T001", "08:00:00", "08:00:00", "WESTLAKE", "1"])
        w.writerow(["T001", "08:05:00", "08:05:00", "PIONEER", "2"])
        w.writerow(["T002", "08:10:00", "08:10:00", "WESTLAKE", "1"])
        w.writerow(["T003", "09:00:00", "09:00:00", "WESTLAKE", "1"])
        w.writerow(["T003", "09:15:00", "09:15:00", "BELLEVUE", "2"])

    # stops.txt
    with open(agency_dir / "stops.txt", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["stop_id", "stop_name", "stop_lat", "stop_lon"])
        w.writerow(["WESTLAKE", "Westlake", "47.611450", "-122.337532"])
        w.writerow(["PIONEER", "Pioneer Square", "47.602139", "-122.331055"])
        w.writerow(["BELLEVUE", "Bellevue Downtown", "47.615285", "-122.192531"])

    # shapes.txt
    with open(agency_dir / "shapes.txt", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence"])
        w.writerow(["S1", "47.611", "-122.337", "1"])
        w.writerow(["S1", "47.602", "-122.331", "2"])
        w.writerow(["S2", "47.602", "-122.331", "1"])
        w.writerow(["S2", "47.611", "-122.337", "2"])
        w.writerow(["S3", "47.611", "-122.337", "1"])
        w.writerow(["S3", "47.615", "-122.192", "2"])

    return tmp_path


@pytest.fixture
def gtfs_db(tmp_path):
    """Create a minimal GTFS SQLite index with a route entry."""
    db_path = tmp_path / "gtfs_index.sqlite"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE routes (
            route_id TEXT PRIMARY KEY, short_name TEXT,
            long_name TEXT, color TEXT, route_type INTEGER
        );
    """)
    conn.execute("INSERT INTO routes VALUES (?, ?, ?, ?, ?)", ("100479", "1 Line", "Lynnwood - Federal Way", "28813F", 0))
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Index Map Tests
# ---------------------------------------------------------------------------


class TestIndexMap:
    """Tests for the full-screen Leaflet index map."""

    def test_renders_leaflet_map(self):
        html = generate_index_html([])
        assert "leaflet.js" in html
        assert "leaflet.css" in html
        assert 'id="map"' in html

    def test_embeds_wsf_stops(self, sample_wsf_stops):
        html = generate_index_html([], sample_wsf_stops)
        assert "Seattle" in html
        assert "Bainbridge Island" in html
        assert "47.6023" in html

    def test_wsdot_bounds(self):
        html = generate_index_html([])
        assert "47.0" in html
        assert "49.0" in html
        assert "-124.0" in html
        assert "-121.5" in html

    def test_modal_structure(self):
        html = generate_index_html([])
        assert 'id="modal-overlay"' in html
        assert 'id="modal-map"' in html
        assert 'id="modal-close"' in html
        assert 'id="modal-title"' in html

    def test_fetches_stations_geojson(self):
        html = generate_index_html([])
        assert "/stations.geojson" in html

    def test_empty_wsf_stops_renders(self):
        html = generate_index_html([], [])
        assert "WSF_STOPS = []" in html

    def test_none_wsf_stops_renders(self):
        html = generate_index_html([])
        assert "WSF_STOPS = []" in html

    def test_rail_color_defined(self):
        html = generate_index_html([])
        assert "#28813F" in html

    def test_osm_tile_layer(self):
        html = generate_index_html([])
        assert "tile.openstreetmap.org" in html


# ---------------------------------------------------------------------------
# Stations GeoJSON Tests
# ---------------------------------------------------------------------------


class TestStationsGeoJSON:
    """Tests for build_stations_geojson() — loading the pre-built file."""

    def test_returns_empty_collection_when_missing(self, tmp_path):
        with patch("transit_tracker.web._PROJECT_ROOT", tmp_path):
            result = json.loads(build_stations_geojson())
        assert result["type"] == "FeatureCollection"
        assert result["features"] == []

    def test_loads_existing_file(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [-122.337, 47.611]},
                "properties": {
                    "name": "Westlake",
                    "walkshed": {"type": "Polygon", "coordinates": [[[-122.34, 47.61], [-122.33, 47.61], [-122.33, 47.62], [-122.34, 47.61]]]},
                },
            }],
        }
        (data_dir / "stations.geojson").write_text(json.dumps(geojson))

        with patch("transit_tracker.web._PROJECT_ROOT", tmp_path):
            result = json.loads(build_stations_geojson())

        assert len(result["features"]) == 1
        assert result["features"][0]["properties"]["name"] == "Westlake"
        assert result["features"][0]["properties"]["walkshed"]["type"] == "Polygon"


# ---------------------------------------------------------------------------
# Route Polylines Tests
# ---------------------------------------------------------------------------


class TestRoutePolylines:
    """Tests for resolve_route_polylines() — GTFS shape resolution."""

    def test_returns_empty_when_no_db(self, tmp_path, mock_config):
        with patch("transit_tracker.web._GTFS_DB", tmp_path / "nonexistent.sqlite"):
            result = resolve_route_polylines(mock_config)
        assert result == []

    def test_resolves_shapes_from_gtfs(self, gtfs_tree, gtfs_db, mock_config):
        with patch("transit_tracker.web._GTFS_DB", gtfs_db), \
             patch("transit_tracker.web._GTFS_DIR", gtfs_tree / "gtfs"):
            result = resolve_route_polylines(mock_config)

        assert len(result) == 1
        assert result[0]["name"] == "1 Line"
        assert result[0]["color"] == "#28813F"
        assert len(result[0]["polylines"]) > 0
        # Each polyline is a list of [lat, lon] pairs
        for polyline in result[0]["polylines"]:
            assert len(polyline) >= 2
            for point in polyline:
                assert len(point) == 2

    def test_deduplicates_routes(self, gtfs_tree, gtfs_db):
        """Same route from multiple subscriptions is only resolved once."""
        config = TransitConfig(
            transit_tracker={
                "base_url": "wss://tt.horner.tj/",
                "stops": [
                    {"stop_id": "st:1_8494", "label": "Stop A", "time_offset": "0min", "routes": ["st:40_100479"]},
                    {"stop_id": "st:1_1920", "label": "Stop B", "time_offset": "0min", "routes": ["st:40_100479"]},
                ],
            }
        )
        with patch("transit_tracker.web._GTFS_DB", gtfs_db), \
             patch("transit_tracker.web._GTFS_DIR", gtfs_tree / "gtfs"):
            result = resolve_route_polylines(config)

        assert len(result) == 1

    def test_strips_agency_prefix(self, gtfs_tree, gtfs_db):
        """Route IDs like 'st:40_100479' are stripped to '100479' for GTFS lookup."""
        config = TransitConfig(
            transit_tracker={
                "base_url": "wss://tt.horner.tj/",
                "stops": [
                    {"stop_id": "st:1_8494", "label": "Test", "time_offset": "0min", "routes": ["st:40_100479"]},
                ],
            }
        )
        with patch("transit_tracker.web._GTFS_DB", gtfs_db), \
             patch("transit_tracker.web._GTFS_DIR", gtfs_tree / "gtfs"):
            result = resolve_route_polylines(config)

        assert len(result) == 1
        assert result[0]["name"] == "1 Line"

    def test_skips_unknown_route(self, gtfs_tree, gtfs_db):
        """Routes not in the GTFS index are silently skipped."""
        config = TransitConfig(
            transit_tracker={
                "base_url": "wss://tt.horner.tj/",
                "stops": [
                    {"stop_id": "st:1_8494", "label": "Test", "time_offset": "0min", "routes": ["st:40_999999"]},
                ],
            }
        )
        with patch("transit_tracker.web._GTFS_DB", gtfs_db), \
             patch("transit_tracker.web._GTFS_DIR", gtfs_tree / "gtfs"):
            result = resolve_route_polylines(config)

        assert result == []


# ---------------------------------------------------------------------------
# Walkshed HTML Tests
# ---------------------------------------------------------------------------


class TestWalkshedHTML:
    """Tests for generate_walkshed_html() — Leaflet walkshed page."""

    def test_renders_leaflet(self, sample_stops):
        html = generate_walkshed_html(sample_stops)
        assert "leaflet.js" in html
        assert "leaflet.css" in html
        assert 'id="map"' in html

    def test_embeds_stop_data(self, sample_stops):
        html = generate_walkshed_html(sample_stops)
        assert "Issaquah TC" in html
        assert "47.5301" in html

    def test_walk_radius(self, sample_stops):
        html = generate_walkshed_html(sample_stops)
        assert "480" in html  # 10 min × 60s × 0.8 m/s

    def test_includes_route_polylines(self, sample_stops, sample_routes):
        html = generate_walkshed_html(sample_stops, sample_routes)
        assert "1 Line" in html
        assert "#28813F" in html

    def test_empty_stops(self):
        html = generate_walkshed_html([])
        assert "STOPS = []" in html

    def test_empty_routes(self, sample_stops):
        html = generate_walkshed_html(sample_stops, [])
        assert "ROUTES = []" in html


# ---------------------------------------------------------------------------
# HTTP Handler GeoJSON Content Type
# ---------------------------------------------------------------------------


class TestGeoJSONHandler:
    """Tests for serving .geojson with correct MIME type."""

    def test_geojson_content_type(self):
        geojson = '{"type":"FeatureCollection","features":[]}'
        TransitWebHandler.routes = {"/stations.geojson": geojson}
        handler = _make_handler("GET", "/stations.geojson")
        assert handler._status_code == 200
        assert "application/geo+json" in handler._content_type

    def test_geojson_body(self):
        geojson = '{"type":"FeatureCollection","features":[]}'
        TransitWebHandler.routes = {"/stations.geojson": geojson}
        handler = _make_handler("GET", "/stations.geojson")
        body = json.loads(handler._body)
        assert body["type"] == "FeatureCollection"

    def test_geojson_cors(self):
        TransitWebHandler.routes = {"/stations.geojson": "{}"}
        handler = _make_handler("GET", "/stations.geojson")
        assert handler._cors_header == "*"


# ---------------------------------------------------------------------------
# Build Script Tests
# ---------------------------------------------------------------------------


class TestBuildStationsScript:
    """Tests for scripts/build_stations_geojson.py station loading logic."""

    def test_load_stations_deduplicates(self, gtfs_tree):
        """Stations with duplicate names are deduplicated."""
        # Add a duplicate Westlake entry with different stop_id
        stops_path = gtfs_tree / "gtfs" / "40" / "stops.txt"
        with open(stops_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow(["WESTLAKE2", "Westlake", "47.611759", "-122.335785"])

        # Also add it to stop_times
        st_path = gtfs_tree / "gtfs" / "40" / "stop_times.txt"
        with open(st_path, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow(["T001", "08:02:00", "08:02:00", "WESTLAKE2", "3"])

        # Import and test the load_stations function
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_stations", str(Path("scripts/build_stations_geojson.py"))
        )
        mod = importlib.util.module_from_spec(spec)

        # Patch GTFS_DIR before loading
        with patch.dict(os.environ, {}, clear=False):
            spec.loader.exec_module(mod)
            mod.GTFS_DIR = gtfs_tree / "gtfs"
            stations = mod.load_stations()

        names = [s["name"] for s in stations]
        assert names.count("Westlake") == 1

    def test_load_stations_has_coordinates(self, gtfs_tree):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "build_stations", str(Path("scripts/build_stations_geojson.py"))
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.GTFS_DIR = gtfs_tree / "gtfs"
        stations = mod.load_stations()

        for s in stations:
            assert "lat" in s and "lon" in s
            assert -90 <= s["lat"] <= 90
            assert -180 <= s["lon"] <= 180


# ---------------------------------------------------------------------------
# Helper (same pattern as test_web.py)
# ---------------------------------------------------------------------------


def _make_handler(method, path):
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
