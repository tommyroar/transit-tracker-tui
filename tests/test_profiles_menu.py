import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import os
import time
from transit_tracker.tui import profiles_menu

pytestmark = pytest.mark.unit

@pytest.mark.asyncio
async def test_profiles_menu_navigation():
    # Mock data
    mock_profiles = ["/path/to/profile1.yaml", "/path/to/profile2.yaml"]
    
    with patch("transit_tracker.tui.list_profiles", return_value=mock_profiles), \
         patch("transit_tracker.tui.profile_detail_submenu", new_callable=AsyncMock) as mock_submenu, \
         patch("questionary.select") as mock_select:
        
        # Scenario: Select profile1, then Back
        mock_select.return_value.ask_async = AsyncMock(side_effect=["/path/to/profile1.yaml", "back"])
        
        console = MagicMock()
        # Use a small wait or timeout if necessary, but here we control the loop with side_effect
        await profiles_menu(console)
        
        assert mock_submenu.call_count == 1
        mock_submenu.assert_called_with("/path/to/profile1.yaml", console)

@pytest.mark.asyncio
async def test_profile_detail_submenu():
    from transit_tracker.tui import profile_detail_submenu
    from transit_tracker.config import TransitConfig, TransitSubscription
    
    config_path = "/path/to/profile1.yaml"
    mock_config = MagicMock(spec=TransitConfig)
    # Using real objects where possible or strictly defined mocks
    mock_sub = TransitSubscription(feed="st", route="1_100", stop="1_123", label="Test Stop")
    mock_config.subscriptions = [mock_sub]
    
    mock_arrivals = [
        {
            "routeId": "1_100",
            "arrivalTime": (time.time() + 300) * 1000, # 5 mins from now in ms
            "isRealtime": True,
            "routeName": "100"
        }
    ]
    
    with patch("transit_tracker.tui.TransitConfig.load", return_value=mock_config), \
         patch("transit_tracker.tui.TransitAPI") as mock_api_class, \
         patch("transit_tracker.tui.get_service_state", return_value={"last_update": time.time()}), \
         patch("transit_tracker.tui.get_last_service_update", return_value="2026-03-14 12:00:00"), \
         patch("questionary.select") as mock_select, \
         patch("transit_tracker.tui.set_last_config_path") as mock_set_last:
        
        mock_api = mock_api_class.return_value
        mock_api.get_arrivals = AsyncMock(return_value=mock_arrivals)
        mock_api.close = AsyncMock()
        
        # Scenario: Refresh once (loop), then Back
        mock_select.return_value.ask_async = AsyncMock(side_effect=["Refresh", "Back"])
        
        console = MagicMock()
        await profile_detail_submenu(config_path, console)
        
        assert mock_api.get_arrivals.call_count >= 1
        # Check if table was printed (console.print)
        assert console.print.called

@pytest.mark.asyncio
async def test_activate_profile():
    from transit_tracker.tui import profile_detail_submenu
    from transit_tracker.config import TransitConfig
    config_path = "/path/to/profile1.yaml"
    mock_config = MagicMock(spec=TransitConfig)
    mock_config.subscriptions = []
    
    with patch("transit_tracker.tui.TransitConfig.load", return_value=mock_config), \
         patch("transit_tracker.tui.TransitAPI") as mock_api_class, \
         patch("transit_tracker.tui.get_service_state", return_value={}), \
         patch("transit_tracker.tui.get_last_service_update", return_value="Never"), \
         patch("questionary.select") as mock_select, \
         patch("transit_tracker.tui.set_last_config_path") as mock_set_last, \
         patch("time.sleep"): # Don't actually sleep
        
        mock_api = mock_api_class.return_value
        mock_api.get_arrivals = AsyncMock(return_value=[])
        mock_api.close = AsyncMock()
        
        # Scenario: Activate, then Back
        mock_select.return_value.ask_async = AsyncMock(side_effect=["Activate Profile (Set as Default)", "Back"])
        
        console = MagicMock()
        await profile_detail_submenu(config_path, console)
        
        mock_set_last.assert_called_with(config_path)
