"""Tests for dynamic dimming schedule reload: file mtime and REST API."""

import json
import os
from io import BytesIO
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from transit_tracker.config import (
    DimmingEntry,
    ServiceSettings,
    _resolve_settings_path,
    load_service_settings,
    save_service_settings,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Config path resolution
# ---------------------------------------------------------------------------


def test_resolve_settings_path_env_override(tmp_path):
    custom = str(tmp_path / "custom_service.yaml")
    with mock.patch.dict(os.environ, {"SERVICE_SETTINGS_PATH": custom}):
        assert _resolve_settings_path() == custom


def test_resolve_settings_path_ignores_empty_env():
    with mock.patch.dict(os.environ, {"SERVICE_SETTINGS_PATH": ""}):
        # Should fall through to normal resolution
        path = _resolve_settings_path()
        assert path != ""


# ---------------------------------------------------------------------------
# File-based hot-reload
# ---------------------------------------------------------------------------


def test_file_reload_picks_up_changes(tmp_path):
    """Overwriting service.yaml triggers reload on next mtime check."""
    from transit_tracker.config import TransitSubscription
    from transit_tracker.network.websocket_server import TransitServer

    settings_file = str(tmp_path / "service.yaml")

    # Write initial schedule
    initial = ServiceSettings(
        dimming_schedule=[DimmingEntry(time="22:00", brightness=20)],
        device_ip="192.168.5.248",
    )
    with open(settings_file, "w") as f:
        yaml.safe_dump(initial.model_dump(exclude_none=True), f)

    config = MagicMock()
    config.subscriptions = []
    config.service = MagicMock()
    config.service.check_interval_seconds = 30
    config.service.oba_api_key = None
    config.service.display_brightness = 128
    config.service.dimming_schedule = initial.dimming_schedule
    config.service.device_ip = "192.168.5.248"
    config.service.request_spacing_ms = 250
    config.service.use_local_api = True
    config.transit_tracker = MagicMock()
    config.transit_tracker.abbreviations = []
    config.transit_tracker.time_display = "arrival"

    with mock.patch(
        "transit_tracker.network.websocket_server._resolve_settings_path",
        return_value=settings_file,
    ):
        server = TransitServer(config)
        assert server._service_settings_mtime > 0

        # Overwrite with new schedule
        updated = ServiceSettings(
            dimming_schedule=[
                DimmingEntry(time="07:00", brightness=255),
                DimmingEntry(time="21:00", brightness=10),
            ],
            device_ip="192.168.5.248",
        )
        # Ensure mtime actually changes (some filesystems have 1s granularity)
        import time
        time.sleep(0.05)
        with open(settings_file, "w") as f:
            yaml.safe_dump(updated.model_dump(exclude_none=True), f)

        with mock.patch(
            "transit_tracker.network.websocket_server.load_service_settings",
            return_value=updated,
        ) as mock_load:
            server._maybe_reload_service_settings()
            mock_load.assert_called_once()


def test_file_reload_no_change_skips(tmp_path):
    """Same mtime means no reload."""
    from transit_tracker.config import TransitSubscription
    from transit_tracker.network.websocket_server import TransitServer

    settings_file = str(tmp_path / "service.yaml")
    with open(settings_file, "w") as f:
        yaml.safe_dump({"display_brightness": 128}, f)

    config = MagicMock()
    config.subscriptions = []
    config.service = MagicMock()
    config.service.check_interval_seconds = 30
    config.service.oba_api_key = None
    config.service.display_brightness = 128
    config.service.dimming_schedule = []
    config.service.device_ip = None
    config.service.request_spacing_ms = 250
    config.service.use_local_api = True
    config.transit_tracker = MagicMock()
    config.transit_tracker.abbreviations = []
    config.transit_tracker.time_display = "arrival"

    with mock.patch(
        "transit_tracker.network.websocket_server._resolve_settings_path",
        return_value=settings_file,
    ):
        server = TransitServer(config)

        with mock.patch(
            "transit_tracker.network.websocket_server.load_service_settings",
        ) as mock_load:
            server._maybe_reload_service_settings()
            mock_load.assert_not_called()


# ---------------------------------------------------------------------------
# REST API /api/dimming
# ---------------------------------------------------------------------------


def _make_handler(method, path, body=None):
    """Create a mock TransitWebHandler for testing."""
    from transit_tracker.web import TransitWebHandler

    handler = MagicMock(spec=TransitWebHandler)
    handler.path = path
    handler.headers = {"Content-Length": str(len(body)) if body else "0"}
    handler.rfile = BytesIO(body) if body else BytesIO(b"")
    handler.wfile = BytesIO()

    # Bind real methods
    handler._json_response = lambda body: TransitWebHandler._json_response(handler, body)
    handler._json_error = lambda code, msg: TransitWebHandler._json_error(handler, code, msg)
    handler._serve_dimming_get = lambda: TransitWebHandler._serve_dimming_get(handler)
    handler._handle_dimming_post = lambda: TransitWebHandler._handle_dimming_post(handler)
    handler.do_POST = lambda: TransitWebHandler.do_POST(handler)

    return handler


def test_rest_get_returns_current(tmp_path):
    svc = ServiceSettings(
        daylight_dimming_enabled=True,
        daylight_dimming_timezone="America/New_York",
        device_ip="10.0.0.1",
        display_brightness=64,
    )

    with mock.patch("transit_tracker.config.load_service_settings", return_value=svc):
        handler = _make_handler("GET", "/api/dimming")
        handler._serve_dimming_get()

    handler.send_response.assert_called_with(200)
    written = handler.wfile.getvalue().decode()
    data = json.loads(written)
    assert data["daylight_dimming_enabled"] is True
    assert data["daylight_dimming_timezone"] == "America/New_York"
    assert data["display_brightness"] == 64
    assert data["device_ip"] == "10.0.0.1"
    assert "computed_schedule" in data  # present when daylight enabled


def test_rest_get_disabled_no_schedule():
    svc = ServiceSettings(daylight_dimming_enabled=False, display_brightness=128)

    with mock.patch("transit_tracker.config.load_service_settings", return_value=svc):
        handler = _make_handler("GET", "/api/dimming")
        handler._serve_dimming_get()

    handler.send_response.assert_called_with(200)
    written = handler.wfile.getvalue().decode()
    data = json.loads(written)
    assert data["daylight_dimming_enabled"] is False
    assert "computed_schedule" not in data


def test_rest_post_enable_daylight():
    initial = ServiceSettings(display_brightness=128)

    body = json.dumps({
        "daylight_dimming_enabled": True,
        "daylight_dimming_timezone": "America/Chicago",
    }).encode()

    with mock.patch("transit_tracker.config.load_service_settings", return_value=initial), \
         mock.patch("transit_tracker.config.save_service_settings") as mock_save:
        handler = _make_handler("POST", "/api/dimming", body)
        handler._handle_dimming_post()

    handler.send_response.assert_called_with(200)
    mock_save.assert_called_once()
    saved = mock_save.call_args[0][0]
    assert saved.daylight_dimming_enabled is True
    assert saved.daylight_dimming_timezone == "America/Chicago"


def test_rest_post_invalid_json():
    handler = _make_handler("POST", "/api/dimming", b"not json")
    handler._handle_dimming_post()

    handler.send_response.assert_called_with(400)
