import json
import os
import sys
import time
from unittest.mock import MagicMock, patch

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

# 1. Fully Mock Rumps before any import
mock_rumps = MagicMock()
mock_rumps.separator = "---"

class MockMenuItem:
    def __init__(self, title, callback=None):
        self.title = title
        self.callback = callback
        self.items = []
    def add(self, item):
        self.items.append(item)
    def clear(self):
        self.items = []
    def __repr__(self):
        return f"MenuItem({self.title})"

mock_rumps.MenuItem = MockMenuItem

class MockApp:
    def __init__(self, name, title=None, quit_button=None):
        self.name = name
        self.title = title
        self.menu = []
    def run(self): pass

mock_rumps.App = MockApp
mock_rumps.Timer = MagicMock()

sys.modules['rumps'] = mock_rumps

# 2. Import the actual app
from transit_tracker.gui import TransitTrackerApp


def test_menu_integrity():
    print("--- GUI Menu Verification ---")
    
    # Mock state data
    mock_state = {
        "last_update": 1000,
        "heartbeat": time.time(),
        "start_time": time.time() - 600,
        "messages_processed": 99,
        "pid": 1234,
        "status": "active",
        "clients": [
            {"address": "127.0.0.1:5000", "name": "Sim"},
            {"address": "10.0.0.5:6000", "name": "HW"}
        ],
        "client_count": 2
    }

    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: json.dumps(mock_state))))), \
         patch("subprocess.run") as mock_run, \
         patch("os.kill", return_value=True):
        
        mock_run.return_value = MagicMock(returncode=0)
        
        app = TransitTrackerApp()
        app.update_state(None)
        
        # Verify Main Menu
        print(f"App Title (Icon): {app.title}")
        
        # Inspect the objects we assigned to self.menu
        found_clients = False
        for item in app.menu:
            title = getattr(item, "title", str(item))
            print(f"Item: {title}")
            
            if "Clients" in str(title):
                found_clients = True
                print(f"  -> Sub-menu detected for: {title}")
                if hasattr(item, 'items') and item.items:
                    for sub in item.items:
                        print(f"     * {sub.title}")
                else:
                    print("     * ERROR: Sub-menu is EMPTY")

        if not found_clients:
            print("ERROR: Clients menu item NOT FOUND in menu list")

if __name__ == "__main__":
    test_menu_integrity()
