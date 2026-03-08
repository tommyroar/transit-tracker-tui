import os
from pydantic import ValidationError
import pytest
from transit_tracker.config import TransitConfig, TransitSubscription

def test_default_config():
    config = TransitConfig()
    assert config.api_url == "wss://tt.horner.tj"
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
    config = TransitConfig(
        arrival_threshold_minutes=10,
        subscriptions=[
            TransitSubscription(feed="st", route="1", stop="2", label="Test Stop")
        ]
    )
    config.save(str(config_path))
    
    assert os.path.exists(config_path)
    
    loaded = TransitConfig.load(str(config_path))
    assert loaded.arrival_threshold_minutes == 10
    assert len(loaded.subscriptions) == 1
    assert loaded.subscriptions[0].label == "Test Stop"
