import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from transit_tracker.tui import main_menu
from transit_tracker.config import TransitConfig

def test_main_menu_service_manager_bug():
    """Reproduction test for NameError in main_menu when selecting Service Manager."""
    
    # Mocking external dependencies used in main_menu
    with patch("transit_tracker.tui.get_last_config_path") as mock_path, \
         patch("transit_tracker.tui.TransitConfig.load") as mock_load, \
         patch("transit_tracker.tui.Console") as mock_console, \
         patch("transit_tracker.tui.make_dashboard") as mock_dash, \
         patch("transit_tracker.tui.get_usb_devices") as mock_usb, \
         patch("questionary.select") as mock_select, \
         patch("transit_tracker.tui.manage_service_menu") as mock_manage, \
         patch("transit_tracker.tui.check_service_status") as mock_status_check:
        
        # Setup mocks
        mock_path.return_value = "config.yaml"
        mock_load.return_value = TransitConfig()
        mock_usb.return_value = []
        mock_status_check.return_value = "STOPPED"
        
        # Configure select to return "Service Manager", then "Exit" to break the loop
        mock_ask_async = AsyncMock(side_effect=["Service Manager", "Exit"])
        mock_select.return_value.ask_async = mock_ask_async
        
        # This should no longer fail with NameError or RuntimeError
        main_menu()

def test_main_menu_simulator_async_fix():
    """Reproduction test for RuntimeError when selecting Simulator from async menu loop."""
    
    with patch("transit_tracker.tui.get_last_config_path") as mock_path, \
         patch("transit_tracker.tui.TransitConfig.load") as mock_load, \
         patch("transit_tracker.tui.Console") as mock_console, \
         patch("transit_tracker.tui.make_dashboard") as mock_dash, \
         patch("transit_tracker.tui.get_usb_devices") as mock_usb, \
         patch("questionary.select") as mock_select, \
         patch("transit_tracker.tui.async_run_simulator") as mock_run_sim, \
         patch("transit_tracker.tui.check_service_status") as mock_status_check:
        
        # Setup mocks
        mock_path.return_value = "config.yaml"
        mock_load.return_value = TransitConfig()
        mock_usb.return_value = []
        mock_status_check.return_value = "STOPPED"
        
        # Configure select to return "Simulator", then "Exit" to break the loop
        mock_ask_async = AsyncMock(side_effect=["Simulator", "Exit"])
        mock_select.return_value.ask_async = mock_ask_async
        
        # Mock async_run_simulator to do nothing
        mock_run_sim.return_value = AsyncMock()()
        
        # This should no longer fail with RuntimeError
        main_menu()
        
        # Verify it was called correctly
        assert mock_run_sim.call_count == 1

if __name__ == "__main__":
    test_main_menu_service_manager_bug()
    test_main_menu_simulator_async_fix()
