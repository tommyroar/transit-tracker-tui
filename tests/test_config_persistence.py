import os
import unittest.mock as mock
import pytest
import asyncio
from transit_tracker.config import TransitConfig, set_last_config_path, get_last_config_path
from transit_tracker.tui import async_main_menu

@pytest.fixture
def mock_env(tmp_path):
    # Mock settings file
    settings_file = tmp_path / "settings.yaml"
    
    # Mock project root to be the tmp_path
    project_root = str(tmp_path)
    
    # Create .local
    local_dir = tmp_path / ".local"
    local_dir.mkdir()
    
    # Create home.yaml and accurate_config.yaml
    home_yaml = local_dir / "home.yaml"
    home_yaml.write_text("transit_tracker: {stops: []}")
    
    accurate_yaml = local_dir / "accurate_config.yaml"
    accurate_yaml.write_text("transit_tracker: {stops: [{stop_id: 'accurate'}]}")
    
    # Use transit_tracker.config.GLOBAL_SETTINGS_FILE
    with mock.patch("transit_tracker.config.GLOBAL_SETTINGS_FILE", str(settings_file)), \
         mock.patch("transit_tracker.tui.os.path.abspath", side_effect=lambda p: str(p)):
        
        yield {
            "settings": settings_file,
            "home": home_yaml,
            "accurate": accurate_yaml,
            "project_root": project_root
        }

def test_tui_persistence_honors_saved_path(mock_env):
    """
    Ensures that async_main_menu uses the saved path even if accurate_config.yaml exists.
    """
    home_path = str(mock_env["home"])
    accurate_path = str(mock_env["accurate"])
    
    # Pre-set the last config path to home.yaml
    set_last_config_path(home_path)
    assert get_last_config_path() == home_path
    
    # Mock ask_with_live_dashboard to exit immediately
    with mock.patch("transit_tracker.tui.ask_with_live_dashboard", side_effect=Exception("Stop")), \
         mock.patch("transit_tracker.tui.get_last_config_path", return_value=home_path), \
         mock.patch("os.path.exists", side_effect=lambda p: str(p) in [home_path, accurate_path, str(mock_env["project_root"]), str(os.path.dirname(home_path))]), \
         mock.patch("transit_tracker.tui.Console"), \
         mock.patch("transit_tracker.config.TransitConfig.load", wraps=TransitConfig.load) as mock_load:
        
        try:
            asyncio.run(async_main_menu())
        except Exception as e:
            if str(e) != "Stop": raise e
            
        # Verify that TransitConfig.load was called with home_path
        found_home_load = False
        for call in mock_load.call_args_list:
            if str(call.args[0]) == home_path:
                found_home_load = True
        
        assert found_home_load, f"Expected load({home_path}), but got calls: {mock_load.call_args_list}"
