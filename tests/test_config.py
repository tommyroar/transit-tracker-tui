import os
import sys

import pytest
import yaml
from pydantic import ValidationError

pytestmark = pytest.mark.unit

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from transit_tracker.config import (
    ServiceSettings,
    TransitConfig,
    TransitTrackerSettings,
    _migrate_legacy_fields,
    load_service_settings,
    save_service_settings,
    set_last_config_path,
)


def test_default_config():
    config = TransitConfig()
    # By default, use_local_api is False, so api_url points to the public endpoint.
    assert config.service.use_local_api is False
    assert config.api_url == "wss://tt.horner.tj/"
    assert config.transit_tracker.base_url == "wss://tt.horner.tj/"
    assert config.service.arrival_threshold_minutes == 5
    assert len(config.subscriptions) == 0


def test_service_settings_validation():
    with pytest.raises(ValidationError):
        # threshold must be >= 1
        ServiceSettings(arrival_threshold_minutes=0)

    with pytest.raises(ValidationError):
        # check interval must be >= 10
        ServiceSettings(check_interval_seconds=5)


def test_config_save_load(tmp_path):
    config_path = tmp_path / "test_config.yaml"
    svc = ServiceSettings(arrival_threshold_minutes=10)
    config = TransitConfig(
        service=svc,
        transit_tracker={
            "stops": [{"stop_id": "2", "label": "Test Stop", "routes": ["st:1"]}]
        },
    )
    config.save(str(config_path))

    assert os.path.exists(config_path)

    # Profile YAML should only contain transit_tracker block
    with open(config_path) as f:
        saved_data = yaml.safe_load(f)
    assert "transit_tracker" in saved_data
    assert "service" not in saved_data
    assert "arrival_threshold_minutes" not in saved_data
    assert "arrival_threshold_minutes" not in saved_data.get("transit_tracker", {})

    loaded = TransitConfig.load(str(config_path), service_settings=svc)
    assert loaded.service.arrival_threshold_minutes == 10
    assert len(loaded.subscriptions) == 1
    assert loaded.subscriptions[0].label == "Test Stop"
    assert loaded.subscriptions[0].stop == "2"


def test_service_settings_roundtrip(tmp_path):
    """Service settings save and load correctly via .local/service.yaml."""
    import unittest.mock as mock
    settings_file = tmp_path / "service.yaml"
    with mock.patch(
        "transit_tracker.config._resolve_settings_path",
        return_value=str(settings_file),
    ):
        svc = ServiceSettings(
            oba_api_key="test-key-123",
            check_interval_seconds=45,
            num_panels=3,
            last_config_path="/some/path.yaml",
        )
        save_service_settings(svc)
        assert settings_file.exists()

        loaded = load_service_settings()
        assert loaded.oba_api_key == "test-key-123"
        assert loaded.check_interval_seconds == 45
        assert loaded.num_panels == 3
        assert loaded.last_config_path == "/some/path.yaml"


def test_set_last_config_path_preserves_dimming(tmp_path):
    """set_last_config_path must not overwrite dimming schedule or device_ip."""
    import unittest.mock as mock
    from transit_tracker.config import DimmingEntry

    settings_file = tmp_path / "service.yaml"
    with mock.patch(
        "transit_tracker.config._resolve_settings_path",
        return_value=str(settings_file),
    ):
        # Set up dimming schedule
        svc = ServiceSettings(
            device_ip="192.168.5.248",
            display_brightness=0,
            dimming_schedule=[
                DimmingEntry(time="07:00", brightness=255),
                DimmingEntry(time="19:00", brightness=0),
            ],
        )
        save_service_settings(svc)

        # Switch profile — this was previously wiping dimming
        set_last_config_path("/config/profiles/adventure.yaml")

        loaded = load_service_settings()
        assert loaded.device_ip == "192.168.5.248"
        assert loaded.display_brightness == 0
        assert len(loaded.dimming_schedule) == 2
        assert loaded.dimming_schedule[0].brightness == 255
        assert loaded.dimming_schedule[1].brightness == 0
        assert loaded.last_config_path == "/config/profiles/adventure.yaml"


def test_save_service_settings_preserves_external_fields(tmp_path):
    """save_service_settings must merge with disk, not overwrite externally-set fields."""
    import unittest.mock as mock
    from transit_tracker.config import DimmingEntry
    import yaml

    settings_file = tmp_path / "service.yaml"
    with mock.patch(
        "transit_tracker.config._resolve_settings_path",
        return_value=str(settings_file),
    ):
        # Write initial settings with API key and dimming via REST-like path
        full = ServiceSettings(
            oba_api_key="real-key-abc",
            device_ip="192.168.5.248",
            dimming_schedule=[DimmingEntry(time="07:00", brightness=255)],
        )
        save_service_settings(full)

        # Simulate a TUI wizard that only knows about a subset of fields
        partial = ServiceSettings(num_panels=3, check_interval_seconds=45)
        save_service_settings(partial)

        loaded = load_service_settings()
        # TUI fields updated
        assert loaded.num_panels == 3
        assert loaded.check_interval_seconds == 45
        # REST-set fields preserved
        assert loaded.oba_api_key == "real-key-abc"
        assert loaded.device_ip == "192.168.5.248"
        assert len(loaded.dimming_schedule) == 1




def test_migrate_legacy_fields():
    """Old-format YAML with service fields embedded gets migrated."""
    data = {
        "use_local_api": True,
        "transit_tracker": {
            "oba_api_key": "legacy-key",
            "check_interval_seconds": 45,
            "num_panels": 3,
            "mapbox_access_token": "dead-token",
            "show_units": "long",
            "list_mode": "sequential",
            "styles": [{"color": "red"}],
            "stops": [{"stop_id": "1", "routes": ["st:1"]}],
        },
    }
    svc = ServiceSettings()
    _migrate_legacy_fields(data, svc)

    # Service fields absorbed
    assert svc.use_local_api is True
    assert svc.oba_api_key == "legacy-key"
    assert svc.check_interval_seconds == 45
    assert svc.num_panels == 3

    # Dead fields stripped
    tt = data["transit_tracker"]
    assert "mapbox_access_token" not in tt
    assert "show_units" not in tt
    assert "list_mode" not in tt
    assert "styles" not in tt

    # Service fields removed from profile data
    assert "oba_api_key" not in tt
    assert "check_interval_seconds" not in tt
    assert "num_panels" not in tt
    assert "use_local_api" not in data

    # Subscription data preserved
    assert tt["stops"] == [{"stop_id": "1", "routes": ["st:1"]}]


def test_load_needle_stops_yaml():
    """The reference config data/needle_stops.yaml loads cleanly."""
    needle_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "needle_stops.yaml"
    )
    if not os.path.exists(needle_path):
        pytest.skip("data/needle_stops.yaml not found")

    config = TransitConfig.load(needle_path)
    assert len(config.subscriptions) == 2
    assert config.transit_tracker.base_url == "wss://tt.horner.tj/"
    assert all(s.feed == "st" for s in config.subscriptions)


def test_transit_tracker_settings_clean_schema():
    """TransitTrackerSettings should NOT have service-level fields."""
    field_names = set(TransitTrackerSettings.model_fields.keys())
    # These should NOT be in the subscription schema
    assert "oba_api_key" not in field_names
    assert "mapbox_access_token" not in field_names
    assert "check_interval_seconds" not in field_names
    assert "request_spacing_ms" not in field_names
    assert "num_panels" not in field_names
    assert "panel_width" not in field_names
    assert "panel_height" not in field_names
    assert "arrival_threshold_minutes" not in field_names
    assert "show_units" not in field_names
    assert "list_mode" not in field_names
    assert "styles" not in field_names
