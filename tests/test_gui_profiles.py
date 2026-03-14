import json
import os
import time
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

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
        self.cache_lock = MagicMock() # Simplified for test
        self.startup_time = time.time()
        self.last_client_ids = None
        self.is_rate_limited = False
        self.last_profiles = []
        self.last_update_ts = 0
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
        
        assert any("Work:" in t for t in titles)
        assert any("File: /path/to/home.yaml" in t for t in titles)

def test_switch_profile_callback(test_app):
    sender = MagicMock()
    sender.p_path = "/path/to/new.yaml"
    
    with patch("transit_tracker.gui.set_last_config_path") as mock_set, \
         patch("rumps.notification"):
        test_app.switch_profile(sender)
        mock_set.assert_called_with("/path/to/new.yaml")
