import socket
from unittest.mock import patch

import pytest

from transit_tracker.config import TransitConfig, TransitStop
from transit_tracker.hardware import EntityType, flash_hardware

pytestmark = pytest.mark.unit

def test_config_serialization_for_hardware():
    """
    Verifies that TransitConfig correctly serializes into the semicolon-separated
    string format expected by the ESP32 hardware.
    """
    config = TransitConfig(
        service={"use_local_api": True},
        transit_tracker={
            "stops": [
                {"stop_id": "st:1_8494", "routes": ["st:40_100240"], "time_offset": "-7min"},
                {"stop_id": "st:1_1002", "routes": ["st:1_100451"], "time_offset": "2min"},
            ]
        },
    )

    # The hardware expects: routeId,stopId,offsetSeconds;...
    # -7min = -420 seconds
    # 2min = 120 seconds
    expected_schedule = "st:40_100240,st:1_8494,-420;st:1_100451,st:1_1002,120"
    expected_base_url = f"ws://{socket.gethostname()}:8000/"

    # Mock ESPHomeFlasher to capture what is sent
    with patch("transit_tracker.hardware.ESPHomeFlasher") as MockFlasher:
        mock_instance = MockFlasher.return_value.__enter__.return_value
        mock_instance.send_request.return_value = {"success": True}

        success = flash_hardware("/dev/tty.mock", config)

        assert success is True

        # Verify base_url_config (should be the machine's hostname URL for ESP32 reachability)
        mock_instance.set_entity.assert_any_call(
            "base_url_config", EntityType.TEXT, expected_base_url
        )

        # Verify schedule_config string format
        mock_instance.set_entity.assert_any_call(
            "schedule_config", EntityType.TEXT, expected_schedule
        )

def test_local_api_url_resolution():
    """Ensures that setting use_local_api=True produces a URL with the machine hostname."""
    config = TransitConfig(service={"use_local_api": True})

    hostname = socket.gethostname()
    assert config.api_url == f"ws://{hostname}:8000/"
    assert config.api_url.startswith("ws://")

if __name__ == "__main__":
    pytest.main([__file__])
