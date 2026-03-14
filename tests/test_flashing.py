from unittest.mock import patch

import pytest

from transit_tracker.config import TransitConfig, TransitStop
from transit_tracker.hardware import EntityType, flash_hardware


def test_config_serialization_for_hardware():
    """
    Verifies that TransitConfig correctly serializes into the semicolon-separated 
    string format expected by the ESP32 hardware.
    """
    config = TransitConfig()
    config.use_local_api = True
    config.transit_tracker.stops = [
        TransitStop(stop_id="st:1_8494", routes=["st:40_100240"], time_offset="-7min"),
        TransitStop(stop_id="st:1_1002", routes=["st:1_100451"], time_offset="2min")
    ]
    config.sync_internal_state()

    # The hardware expects: routeId,stopId,offsetSeconds;...
    # -7min = -420 seconds
    # 2min = 120 seconds
    expected_schedule = "st:40_100240,st:1_8494,-420;st:1_100451,st:1_1002,120"
    
    # Mock ESPHomeFlasher to capture what is sent
    with patch("transit_tracker.hardware.ESPHomeFlasher") as MockFlasher:
        mock_instance = MockFlasher.return_value.__enter__.return_value
        mock_instance.send_request.return_value = {"success": True}
        
        success = flash_hardware("/dev/tty.mock", config)
        
        assert success is True
        
        # Verify base_url_config (should be the local .local URL)
        mock_instance.set_entity.assert_any_call(
            "base_url_config", EntityType.TEXT, "ws://Tommys-Mac-mini.local:8000/"
        )
        
        # Verify schedule_config string format
        mock_instance.set_entity.assert_any_call(
            "schedule_config", EntityType.TEXT, expected_schedule
        )

def test_local_api_url_resolution():
    """Ensures that setting use_local_api=True always results in a network-reachable URL."""
    config = TransitConfig()
    config.use_local_api = True
    config.sync_internal_state()
    
    assert "localhost" not in config.api_url
    assert "Tommys-Mac-mini.local" in config.api_url
    assert config.api_url.startswith("ws://")

if __name__ == "__main__":
    pytest.main([__file__])
