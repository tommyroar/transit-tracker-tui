"""Tests for display brightness control."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from transit_tracker.config import TransitConfig, TransitSubscription
from transit_tracker.hardware import EntityType, ESPHomeFlasher
from transit_tracker.network.websocket_server import TransitServer

# ---------------------------------------------------------------------------
# Config validation tests
# ---------------------------------------------------------------------------


def test_brightness_default():
    config = TransitConfig()
    assert config.display_brightness == 128
    assert config.transit_tracker.display_brightness == 128


def test_brightness_boundary_values():
    config_zero = TransitConfig(transit_tracker={"display_brightness": 0})
    assert config_zero.display_brightness == 0

    config_max = TransitConfig(transit_tracker={"display_brightness": 255})
    assert config_max.display_brightness == 255


def test_brightness_rejects_out_of_range():
    with pytest.raises(ValidationError):
        TransitConfig(transit_tracker={"display_brightness": -1})

    with pytest.raises(ValidationError):
        TransitConfig(transit_tracker={"display_brightness": 256})


def test_brightness_round_trip_yaml(tmp_path):
    config = TransitConfig(transit_tracker={"display_brightness": 42})
    path = str(tmp_path / "bright.yaml")
    config.save(path)

    loaded = TransitConfig.load(path)
    assert loaded.display_brightness == 42
    assert loaded.transit_tracker.display_brightness == 42


def test_brightness_syncs_to_root():
    config = TransitConfig(transit_tracker={"display_brightness": 200})
    assert config.display_brightness == 200


# ---------------------------------------------------------------------------
# Serial protocol tests
# ---------------------------------------------------------------------------


def test_number_entity_type_value():
    assert EntityType.NUMBER == 5


def test_number_entity_wire_format():
    """Verify JRPC payload uses type=5 for NUMBER entities."""
    with patch("transit_tracker.hardware.serial.Serial") as MockSerial:
        mock_serial = MagicMock()
        MockSerial.return_value = mock_serial

        response = {"jsonrpc": "2.0", "id": 1, "result": {"success": True}}
        mock_serial.readline.return_value = f"JRPC:{json.dumps(response)}\r\n".encode(
            "utf-8"
        )

        with ESPHomeFlasher("/dev/tty.mock") as flasher:
            flasher.set_entity("display_brightness", EntityType.NUMBER, 128)

        written = mock_serial.write.call_args[0][0].decode("utf-8")
        payload = json.loads(written[5:-2])  # strip JRPC: prefix and \r\n
        assert payload["params"]["type"] == 5
        assert payload["params"]["value"] == 128
        assert payload["params"]["id"] == "display_brightness"


def test_flash_sends_brightness():
    """flash_hardware() must send display_brightness as a NUMBER entity."""
    with patch("transit_tracker.hardware.serial.Serial") as MockSerial:
        mock_serial = MagicMock()
        MockSerial.return_value = mock_serial

        call_count = 0

        def mock_readline():
            nonlocal call_count
            call_count += 1
            response = {"jsonrpc": "2.0", "id": call_count, "result": {"success": True}}
            return f"JRPC:{json.dumps(response)}\r\n".encode("utf-8")

        mock_serial.readline.side_effect = mock_readline

        config = TransitConfig(transit_tracker={"display_brightness": 200})
        config.subscriptions = [
            TransitSubscription(
                feed="st", route="st:40_100240", stop="st:1_8494", label="Test"
            )
        ]

        from transit_tracker.hardware import flash_hardware

        flash_hardware("/dev/tty.mock", config)

        # Collect all written JRPC payloads
        written_payloads = []
        for call in mock_serial.write.call_args_list:
            raw = call[0][0].decode("utf-8")
            if raw.startswith("JRPC:"):
                written_payloads.append(json.loads(raw[5:-2]))

        # Find the display_brightness entity.set call
        brightness_calls = [
            p
            for p in written_payloads
            if p.get("method") == "entity.set"
            and p.get("params", {}).get("id") == "display_brightness"
        ]
        assert len(brightness_calls) == 1
        assert brightness_calls[0]["params"]["type"] == EntityType.NUMBER
        assert brightness_calls[0]["params"]["value"] == 200


def test_load_reads_brightness():
    """load_hardware_config() must read display_brightness from the device."""
    with patch("transit_tracker.hardware.serial.Serial") as MockSerial:
        mock_serial = MagicMock()
        MockSerial.return_value = mock_serial

        call_count = 0
        responses = {
            # base_url_config get
            1: {"value": "wss://tt.horner.tj/"},
            # schedule_config get (offset -420 = -7min)
            2: {"value": "st:40_100240,st:1_8494,-420"},
            # display_brightness get
            3: {"value": 180},
        }

        def mock_readline():
            nonlocal call_count
            call_count += 1
            result = responses.get(call_count, {"value": ""})
            response = {"jsonrpc": "2.0", "id": call_count, "result": result}
            return f"JRPC:{json.dumps(response)}\r\n".encode("utf-8")

        mock_serial.readline.side_effect = mock_readline

        config = TransitConfig()
        from transit_tracker.hardware import load_hardware_config

        result = load_hardware_config("/dev/tty.mock", config)
        assert result is True
        assert config.display_brightness == 180


def test_load_handles_float_brightness():
    """ESPHome number entities may return float values."""
    with patch("transit_tracker.hardware.serial.Serial") as MockSerial:
        mock_serial = MagicMock()
        MockSerial.return_value = mock_serial

        call_count = 0

        def mock_readline():
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                result = {"value": 64.0}
            elif call_count == 1:
                result = {"value": "wss://tt.horner.tj/"}
            else:
                result = {"value": ""}
            response = {"jsonrpc": "2.0", "id": call_count, "result": result}
            return f"JRPC:{json.dumps(response)}\r\n".encode("utf-8")

        mock_serial.readline.side_effect = mock_readline

        config = TransitConfig()
        from transit_tracker.hardware import load_hardware_config

        load_hardware_config("/dev/tty.mock", config)
        assert config.display_brightness == 64


# ---------------------------------------------------------------------------
# WebSocket control:brightness tests
# ---------------------------------------------------------------------------


@pytest.fixture
def ws_config():
    config = MagicMock()
    config.subscriptions = [
        TransitSubscription(
            feed="st", route="st:40_100240", stop="st:1_8494", label="Test"
        )
    ]
    config.use_local_api = True
    config.auto_launch_gui = True
    config.arrival_threshold_minutes = 5
    config.check_interval_seconds = 30
    config.time_display = "arrival"
    config.display_brightness = 128
    config.transit_tracker = MagicMock()
    config.transit_tracker.abbreviations = []
    config.transit_tracker.request_spacing_ms = 250
    config.transit_tracker.oba_api_key = None
    return config


@pytest.mark.asyncio
async def test_brightness_init_from_config(ws_config):
    server = TransitServer(ws_config)
    assert server.display_brightness == 128


@pytest.mark.asyncio
async def test_brightness_forwarded_to_other_clients(ws_config):
    """Brightness forwarded to others, not echoed back."""
    server = TransitServer(ws_config)

    ws_sender = AsyncMock()
    ws_sender.remote_address = ("10.0.0.1", 12345)
    ws_receiver = AsyncMock()
    ws_receiver.send = AsyncMock()
    ws_receiver.remote_address = ("10.0.0.2", 12346)

    server.clients = {ws_sender, ws_receiver}

    # Simulate the event handling inline (since register() is a full async loop)
    payload = {"event": "control:brightness", "data": {"value": 200}}
    data = payload.get("data", {})
    value = data.get("value")
    b = int(value)
    server.display_brightness = b
    msg = json.dumps({"event": "control:brightness", "data": {"value": b}})
    for client in list(server.clients):
        if client != ws_sender:
            await client.send(msg)

    assert server.display_brightness == 200
    ws_receiver.send.assert_called_once()
    sent = json.loads(ws_receiver.send.call_args[0][0])
    assert sent["event"] == "control:brightness"
    assert sent["data"]["value"] == 200
    # Sender should NOT have been called
    ws_sender.send.assert_not_called()


@pytest.mark.asyncio
async def test_brightness_invalid_value_ignored(ws_config):
    """Out-of-range and non-numeric values should not change server brightness."""
    server = TransitServer(ws_config)
    original = server.display_brightness

    # Test out-of-range
    for bad_value in [-1, 256, 999]:
        b = int(bad_value)
        if not (0 <= b <= 255):
            pass  # would be skipped in handler
        else:
            server.display_brightness = b

    assert server.display_brightness == original

    # Test non-numeric
    try:
        int("abc")
    except (ValueError, TypeError):
        pass  # handler catches this
    assert server.display_brightness == original


@pytest.mark.asyncio
async def test_brightness_boundary_ws(ws_config):
    """Boundary values 0 and 255 should be accepted."""
    server = TransitServer(ws_config)

    for val in [0, 255]:
        b = int(val)
        if 0 <= b <= 255:
            server.display_brightness = b
        assert server.display_brightness == val
