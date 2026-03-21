import json
import os
import sys
import time
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

if sys.platform != "darwin":
    pytest.skip("GUI tests require macOS (rumps)", allow_module_level=True)

# Define a completely isolated MockApp for testing logic without rumps inheritance
class MockApp:
    def __init__(self):
        self.status_item = MagicMock()
        self.last_update_item = MagicMock()
        self.stats_item = MagicMock()
        self.profiles_menu = MagicMock()
        self.rate_limit_item = MagicMock()
        self.clients_menu = MagicMock()
        self.restart_item = MagicMock()
        self.shutdown_item = MagicMock()

        self.api = MagicMock()
        self.arrivals_cache = {}
        self.display_trips = []
        self.cache_lock = MagicMock() # Simplified for test
        self.startup_time = time.time()
        self.last_client_ids = None
        self.is_rate_limited = False
        self.last_profiles = []
        self.last_update_ts = 0
        self.display_format = None
        self.profile_previews = {}
        self.title = "🚉"

    # Copy the logic from gui.py but make it testable
    from transit_tracker.gui import TransitTrackerApp
    update_state = TransitTrackerApp.update_state
    switch_profile = TransitTrackerApp.switch_profile

@pytest.fixture
def test_app():
    return MockApp()

def test_gui_profile_menu_building(test_app):
    """Test that the GUI correctly builds the profiles submenu with arrivals."""
    mock_profiles = ["/path/to/home.yaml"]

    from transit_tracker.config import TransitConfig, TransitSubscription
    mock_config = MagicMock(spec=TransitConfig)
    mock_sub = TransitSubscription(
        feed="st",
        route="1_100",
        stop="1_123",
        label="Work",
        direction="N"
    )
    mock_config.subscriptions = [mock_sub]

    test_app.arrivals_cache = {
        "1_123": [{"arrivalTime": (time.time() + 600) * 1000, "routeId": "1_100"}]
    }
    test_app.display_trips = [
        {"routeName": "554", "headsign": "Downtown Seattle", "arrivalTime": int(time.time()) + 600, "isRealtime": True},
    ]

    def create_menu_item(title, **kwargs):
        m = MagicMock()
        if isinstance(title, str):
            m.title = title
        else:
            # Handle separator or other non-string cases
            m.title = ""
        return m

    with patch("transit_tracker.gui.list_profiles", return_value=mock_profiles), \
         patch("transit_tracker.gui.TransitConfig.load", return_value=mock_config), \
         patch("transit_tracker.gui.get_last_config_path", return_value="/path/to/home.yaml"), \
         patch("transit_tracker.gui.get_service_state", return_value={"last_update": time.time()}), \
         patch("transit_tracker.gui.get_last_service_update", return_value="12:00:00"), \
         patch("transit_tracker.gui.PLIST_NAME", "test.plist"), \
         patch("rumps.MenuItem", side_effect=create_menu_item), \
         patch("rumps.separator", MagicMock()), \
         patch("subprocess.run") as mock_run:

        mock_run.return_value.returncode = 0

        test_app.update_state(None)

        profile_root = test_app.profiles_menu.add.call_args_list[0][0][0]

        titles = []
        for call in profile_root.add.call_args_list:
            item = call[0][0]
            # In our mock, if title was a string, item.title is that string
            # If it was a mock (separator), item.title might be a Mock or ""
            if hasattr(item, 'title') and isinstance(item.title, str):
                titles.append(item.title)

        # Active profile shows simulator-style rows: "route  headsign  ◉ Xm"
        assert any("554" in t and "Downtown Seattle" in t and "◉" in t for t in titles)
        assert any("File: /path/to/home.yaml" in t for t in titles)

def test_format_trip_line_realtime():
    """Realtime trip shows filled circle and minutes countdown."""
    from transit_tracker.gui import format_trip_line
    now = 1700000000.0
    trip = {"routeName": "554", "headsign": "Downtown Seattle", "arrivalTime": 1700000600, "isRealtime": True}
    line = format_trip_line(trip, now)
    assert line == "554  Downtown Seattle  ◉ 10m"

def test_format_trip_line_scheduled():
    """Scheduled (non-realtime) trip shows empty circle."""
    from transit_tracker.gui import format_trip_line
    now = 1700000000.0
    trip = {"routeName": "14", "headsign": "Summit", "arrivalTime": 1700001800, "isRealtime": False}
    line = format_trip_line(trip, now)
    assert line == "14  Summit  ○ 30m"

def test_format_trip_line_now():
    """Trip arriving now or in the past shows 'Now'."""
    from transit_tracker.gui import format_trip_line
    now = 1700000000.0
    trip = {"routeName": "E", "headsign": "Aurora Village", "arrivalTime": 1699999990, "isRealtime": True}
    line = format_trip_line(trip, now)
    assert line == "E  Aurora Village  ◉ Now"

def test_format_trip_line_millis():
    """Arrival time in milliseconds is handled correctly."""
    from transit_tracker.gui import format_trip_line
    now = 1700000000.0
    trip = {"routeName": "554", "headsign": "Bellevue", "arrivalTime": 1700000300000, "isRealtime": True}
    line = format_trip_line(trip, now)
    assert line == "554  Bellevue  ◉ 5m"

def test_format_trip_line_ferry_vessel():
    """Ferry trip with vessel name headsign formats correctly."""
    from transit_tracker.gui import format_trip_line
    now = 1700000000.0
    trip = {"routeName": "SEA-BI", "headsign": "Puyallup", "arrivalTime": 1700000360, "isRealtime": True}
    line = format_trip_line(trip, now)
    assert line == "SEA-BI  Puyallup  ◉ 6m"

def test_format_trip_line_missing_fields():
    """Missing fields produce sensible defaults."""
    from transit_tracker.gui import format_trip_line
    now = 1700000000.0
    line = format_trip_line({}, now)
    assert line == "?    ○ Now"

def test_switch_profile_callback(test_app):
    sender = MagicMock()
    sender.p_path = "/path/to/new.yaml"

    with patch("transit_tracker.gui.set_last_config_path") as mock_set, \
         patch("transit_tracker.gui.TransitConfig"), \
         patch("rumps.notification"):
        test_app.switch_profile(sender)
        mock_set.assert_called_with("/path/to/new.yaml")


def _sample_trip(**overrides):
    base = {
        "routeName": "554",
        "headsign": "Downtown Seattle",
        "arrivalTime": 1700000600,
        "isRealtime": True,
    }
    base.update(overrides)
    return base


def test_format_trip_line_custom_template():
    """Custom template renders correctly."""
    from transit_tracker.display import format_trip_line
    now = 1700000000.0
    trip = _sample_trip()
    fmt = "{ROUTE} \u2192 {HEADSIGN} {TIME}"
    assert format_trip_line(trip, now, fmt=fmt) == (
        "554 \u2192 Downtown Seattle 10m"
    )


def test_format_trip_line_invalid_template_fallback():
    """Invalid template falls back to default format."""
    from transit_tracker.display import format_trip_line
    now = 1700000000.0
    trip = _sample_trip()
    line = format_trip_line(trip, now, fmt="{NONEXISTENT}")
    assert "554" in line
    assert "Downtown Seattle" in line


def test_build_trip_variables_keys():
    """Variables dict contains all documented keys."""
    from transit_tracker.display import (
        DISPLAY_VARIABLES,
        build_trip_variables,
    )
    now = 1700000000.0
    trip = _sample_trip(headsign="Test")
    variables = build_trip_variables(trip, now)
    for key in DISPLAY_VARIABLES:
        assert key in variables


def test_display_format_in_config():
    """Config model accepts and preserves display_format."""
    from transit_tracker.config import TransitTrackerSettings
    s = TransitTrackerSettings(
        display_format="{ROUTE} {TIME}"
    )
    assert s.display_format == "{ROUTE} {TIME}"


def test_display_format_default_matches_legacy():
    """Default format produces identical output to legacy."""
    from transit_tracker.display import (
        DEFAULT_DISPLAY_FORMAT,
        format_trip_line,
    )
    now = 1700000000.0
    trip = _sample_trip()
    expected = "554  Downtown Seattle  \u25c9 10m"
    assert format_trip_line(trip, now) == expected
    assert format_trip_line(
        trip, now, fmt=DEFAULT_DISPLAY_FORMAT
    ) == expected


def test_format_trip_line_live_only_template():
    """Template using only LIVE and WAIT variables."""
    from transit_tracker.display import format_trip_line
    now = 1700000000.0
    trip = _sample_trip(
        routeName="E",
        headsign="Aurora",
        arrivalTime=1700000300,
        isRealtime=False,
    )
    line = format_trip_line(trip, now, fmt="{LIVE} {WAIT}min")
    assert line == "\u25cb 5min"
