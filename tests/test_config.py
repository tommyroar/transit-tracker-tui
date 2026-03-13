import os
from pydantic import ValidationError
import pytest
import sys

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from transit_tracker.config import TransitConfig, TransitSubscription

def test_default_config():
    config = TransitConfig()
    # By default, use_local_api is False, so api_url points to the public endpoint.
    assert config.use_local_api is False
    assert config.api_url == "wss://tt.horner.tj/"
    assert config.transit_tracker.base_url == "wss://tt.horner.tj/"
    assert config.arrival_threshold_minutes == 5
    assert len(config.subscriptions) == 0

def test_config_validation():
    with pytest.raises(ValidationError):
        # threshold must be >= 1
        TransitConfig(arrival_threshold_minutes=0)
        
    with pytest.raises(ValidationError):
        # check interval must be >= 10
        TransitConfig(check_interval_seconds=5)

def test_config_save_load(tmp_path):
    config_path = tmp_path / "test_config.yaml"
    # Use the nested transit_tracker structure as it's the source of truth
    config = TransitConfig(
        arrival_threshold_minutes=10,
        transit_tracker={
            "stops": [
                {
                    "stop_id": "2",
                    "label": "Test Stop",
                    "routes": ["st:1"]
                }
            ]
        }
    )
    config.save(str(config_path))
    
    assert os.path.exists(config_path)
    
    loaded = TransitConfig.load(str(config_path))
    assert loaded.arrival_threshold_minutes == 10
    assert len(loaded.subscriptions) == 1
    assert loaded.subscriptions[0].label == "Test Stop"
    assert loaded.subscriptions[0].stop == "2"
