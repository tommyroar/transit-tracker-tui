"""Tests for scheduled dimming control via ESPHome REST API."""

import datetime
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from transit_tracker.config import (
    DimmingEntry,
    TransitConfig,
    TransitSubscription,
    evaluate_dimming_schedule,
)
from transit_tracker.network.websocket_server import TransitServer

# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------


def test_dimming_schedule_empty_by_default():
    config = TransitConfig()
    assert config.service.dimming_schedule == []


def test_device_ip_optional():
    config = TransitConfig()
    assert config.service.device_ip is None

    config2 = TransitConfig(service={"device_ip": "192.168.5.248"})
    assert config2.service.device_ip == "192.168.5.248"


def test_dimming_entry_valid():
    entry = DimmingEntry(time="22:00", brightness=20)
    assert entry.time == "22:00"
    assert entry.brightness == 20


def test_dimming_entry_rejects_bad_time():
    with pytest.raises(ValidationError):
        DimmingEntry(time="25:00", brightness=20)

    with pytest.raises(ValidationError):
        DimmingEntry(time="7:00", brightness=20)  # missing leading zero

    with pytest.raises(ValidationError):
        DimmingEntry(time="abc", brightness=20)

    with pytest.raises(ValidationError):
        DimmingEntry(time="12:60", brightness=20)


def test_dimming_entry_rejects_bad_brightness():
    with pytest.raises(ValidationError):
        DimmingEntry(time="22:00", brightness=-1)

    with pytest.raises(ValidationError):
        DimmingEntry(time="22:00", brightness=256)


def test_dimming_schedule_round_trip_service_settings(tmp_path):
    """dimming_schedule and device_ip are ServiceSettings fields."""
    from transit_tracker.config import (
        ServiceSettings,
        load_service_settings,
        save_service_settings,
    )
    from unittest import mock
    import os

    settings_file = tmp_path / "service.yaml"
    with mock.patch("transit_tracker.config.SERVICE_SETTINGS_FILE", str(settings_file)), \
         mock.patch.dict(os.environ, {"TRANSIT_TRACKER_TESTING": "0"}):
        svc = ServiceSettings(
            device_ip="192.168.5.248",
            dimming_schedule=[
                DimmingEntry(time="22:00", brightness=20),
                DimmingEntry(time="07:00", brightness=128),
            ],
        )
        save_service_settings(svc)
        loaded = load_service_settings()
        assert len(loaded.dimming_schedule) == 2
        assert loaded.dimming_schedule[0].time == "22:00"
        assert loaded.dimming_schedule[0].brightness == 20
        assert loaded.device_ip == "192.168.5.248"


# ---------------------------------------------------------------------------
# Schedule evaluation tests (pure function)
# ---------------------------------------------------------------------------


def test_evaluate_empty_schedule():
    assert evaluate_dimming_schedule([], datetime.time(12, 0)) is None


def test_evaluate_single_entry():
    schedule = [DimmingEntry(time="12:00", brightness=100)]
    # Any time should return that entry's brightness
    assert evaluate_dimming_schedule(schedule, datetime.time(0, 0)) == 100
    assert evaluate_dimming_schedule(schedule, datetime.time(12, 0)) == 100
    assert evaluate_dimming_schedule(schedule, datetime.time(23, 59)) == 100


def test_evaluate_daytime():
    schedule = [
        DimmingEntry(time="07:00", brightness=128),
        DimmingEntry(time="22:00", brightness=20),
    ]
    # At noon, the 07:00 entry is the most recent past entry
    assert evaluate_dimming_schedule(schedule, datetime.time(12, 0)) == 128


def test_evaluate_nighttime():
    schedule = [
        DimmingEntry(time="07:00", brightness=128),
        DimmingEntry(time="22:00", brightness=20),
    ]
    # At 23:00, the 22:00 entry is the most recent past entry
    assert evaluate_dimming_schedule(schedule, datetime.time(23, 0)) == 20


def test_evaluate_midnight_wraparound():
    schedule = [
        DimmingEntry(time="07:00", brightness=128),
        DimmingEntry(time="22:00", brightness=20),
    ]
    # At 03:00, no entry is at/before now, so wrap to last (22:00 = 20)
    assert evaluate_dimming_schedule(schedule, datetime.time(3, 0)) == 20


def test_evaluate_exact_boundary():
    schedule = [
        DimmingEntry(time="07:00", brightness=128),
        DimmingEntry(time="22:00", brightness=20),
    ]
    # At exactly 22:00, the 22:00 entry should apply
    assert evaluate_dimming_schedule(schedule, datetime.time(22, 0)) == 20
    # At exactly 07:00, the 07:00 entry should apply
    assert evaluate_dimming_schedule(schedule, datetime.time(7, 0)) == 128


def test_evaluate_multiple_entries():
    schedule = [
        DimmingEntry(time="06:00", brightness=60),
        DimmingEntry(time="08:00", brightness=128),
        DimmingEntry(time="20:00", brightness=80),
        DimmingEntry(time="23:00", brightness=10),
    ]
    assert evaluate_dimming_schedule(schedule, datetime.time(5, 0)) == 10  # wrap
    assert evaluate_dimming_schedule(schedule, datetime.time(7, 0)) == 60
    assert evaluate_dimming_schedule(schedule, datetime.time(12, 0)) == 128
    assert evaluate_dimming_schedule(schedule, datetime.time(21, 0)) == 80
    assert evaluate_dimming_schedule(schedule, datetime.time(23, 30)) == 10


# ---------------------------------------------------------------------------
# Dimming loop integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def dimming_config():
    config = MagicMock()
    config.subscriptions = [
        TransitSubscription(
            feed="st", route="st:40_100240", stop="st:1_8494", label="Test"
        )
    ]
    config.service = MagicMock()
    config.service.use_local_api = True
    config.service.auto_launch_gui = True
    config.service.arrival_threshold_minutes = 5
    config.service.check_interval_seconds = 30
    config.service.request_spacing_ms = 250
    config.transit_tracker = MagicMock()
    config.transit_tracker.time_display = "arrival"
    config.service.display_brightness = 128
    config.service.oba_api_key = None
    config.transit_tracker.abbreviations = []
    config.service.device_ip = "192.168.5.248"
    config.service.dimming_schedule = [
        DimmingEntry(time="07:00", brightness=128),
        DimmingEntry(time="22:00", brightness=20),
    ]
    return config


@pytest.mark.asyncio
async def test_apply_posts_to_esphome(dimming_config):
    """Dimming schedule should POST brightness to ESPHome REST API."""
    server = TransitServer(dimming_config)
    server.display_brightness = 128  # current

    mock_client = AsyncMock()

    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = datetime.time(23, 0)
        await server._apply_dimming_schedule(mock_client)

    assert server.display_brightness == 20
    mock_client.post.assert_called_once_with(
        "http://192.168.5.248/light/display_brightness/turn_on?brightness=20",
        headers={"Content-Length": "0"},
    )


@pytest.mark.asyncio
async def test_apply_broadcasts_ws(dimming_config):
    """Dimming schedule should broadcast control:brightness to all WS clients."""
    server = TransitServer(dimming_config)
    server.display_brightness = 128

    ws1 = AsyncMock()
    ws1.remote_address = ("10.0.0.1", 12345)
    ws2 = AsyncMock()
    ws2.remote_address = ("10.0.0.2", 12346)
    server.clients = {ws1, ws2}

    mock_client = AsyncMock()

    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = datetime.time(23, 0)
        await server._apply_dimming_schedule(mock_client)

    # Both clients should receive the brightness broadcast
    assert ws1.send.call_count == 1
    assert ws2.send.call_count == 1
    sent = json.loads(ws1.send.call_args[0][0])
    assert sent == {"event": "control:brightness", "data": {"value": 20}}


@pytest.mark.asyncio
async def test_apply_skips_when_no_schedule(dimming_config):
    """No schedule means no POST and no broadcast."""
    dimming_config.service.dimming_schedule = []
    server = TransitServer(dimming_config)
    mock_client = AsyncMock()

    result = await server._apply_dimming_schedule(mock_client)
    assert result is False
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_apply_manual_override_respected(dimming_config):
    """Manual WS brightness override should prevent schedule from reverting."""
    server = TransitServer(dimming_config)
    server.display_brightness = 200
    server.dimming_override = True
    server.last_scheduled_brightness = 20  # same as schedule target

    mock_client = AsyncMock()

    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = datetime.time(23, 0)
        result = await server._apply_dimming_schedule(mock_client)

    assert result is False
    assert server.display_brightness == 200  # unchanged
    mock_client.post.assert_not_called()


@pytest.mark.asyncio
async def test_apply_override_cleared_on_transition(dimming_config):
    """Override should clear when schedule transitions to a new brightness."""
    server = TransitServer(dimming_config)
    server.display_brightness = 200  # manually set
    server.dimming_override = True
    server.last_scheduled_brightness = 20  # was dim, now should be bright

    mock_client = AsyncMock()

    # Move to daytime — schedule target is 128, different from last_scheduled (20)
    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = datetime.time(12, 0)
        result = await server._apply_dimming_schedule(mock_client)

    assert result is True
    assert server.dimming_override is False
    assert server.display_brightness == 128
    mock_client.post.assert_called_once()


@pytest.mark.asyncio
async def test_apply_handles_http_failure(dimming_config):
    """HTTP POST failure should not crash the loop."""
    server = TransitServer(dimming_config)
    server.display_brightness = 128

    mock_client = AsyncMock()
    mock_client.post.side_effect = Exception("Connection refused")

    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = datetime.time(23, 0)
        # Should not raise
        result = await server._apply_dimming_schedule(mock_client)

    assert result is True
    assert server.display_brightness == 20  # still updated locally


@pytest.mark.asyncio
async def test_apply_no_device_ip_skips_post(dimming_config):
    """Without device_ip, HTTP POST is skipped but WS broadcast still happens."""
    dimming_config.service.device_ip = None
    server = TransitServer(dimming_config)
    server.display_brightness = 128

    ws1 = AsyncMock()
    ws1.remote_address = ("10.0.0.1", 12345)
    server.clients = {ws1}

    mock_client = AsyncMock()

    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = datetime.time(23, 0)
        result = await server._apply_dimming_schedule(mock_client)

    assert result is True
    mock_client.post.assert_not_called()
    ws1.send.assert_called_once()


@pytest.mark.asyncio
async def test_apply_no_change_when_already_at_target(dimming_config):
    """Should not POST or broadcast if brightness already matches schedule."""
    server = TransitServer(dimming_config)
    server.display_brightness = 20  # already at night brightness

    mock_client = AsyncMock()

    with patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value.time.return_value = datetime.time(23, 0)
        result = await server._apply_dimming_schedule(mock_client)

    assert result is False
    mock_client.post.assert_not_called()
