import pytest
import questionary
from unittest.mock import MagicMock, patch, AsyncMock
from transit_tracker.config import TransitConfig
from transit_tracker.tui import (
    change_api_mode_wizard, 
    change_threshold_wizard, 
    change_panels_wizard, 
    change_ntfy_wizard
)

@pytest.fixture
def mock_config():
    config = TransitConfig()
    config.use_local_api = True
    config.arrival_threshold_minutes = 5
    config.num_panels = 2
    config.ntfy_topic = "test-topic"
    return config

@pytest.mark.asyncio
async def test_api_mode_wizard_choices(mock_config):
    """Verify that change_api_mode_wizard uses correct default value for questionary.select."""
    with patch("questionary.select") as mock_select, \
         patch("questionary.text") as mock_text, \
         patch.object(TransitConfig, "save") as mock_save:
        
        # Mocking the select behavior
        mock_instance = MagicMock()
        mock_instance.ask_async = AsyncMock(return_value=True)
        mock_select.return_value = mock_instance
        
        # Test with use_local_api = True
        mock_config.use_local_api = True
        await change_api_mode_wizard(mock_config, "config.yaml")
        
        args, kwargs = mock_select.call_args
        assert kwargs["default"] is True
        
        # Test with use_local_api = False
        mock_config.use_local_api = False
        mock_instance.ask_async = AsyncMock(return_value=False)
        # When False, it asks for a text URL
        mock_text.return_value.ask_async = AsyncMock(return_value="wss://test.api")
        
        await change_api_mode_wizard(mock_config, "config.yaml")
        args, kwargs = mock_select.call_args
        assert kwargs["default"] is False

@pytest.mark.asyncio
async def test_panels_wizard_choices(mock_config):
    """Verify that change_panels_wizard uses correct default value format."""
    with patch("questionary.select") as mock_select, \
         patch.object(TransitConfig, "save") as mock_save:
        mock_instance = MagicMock()
        mock_instance.ask_async = AsyncMock(return_value="3")
        mock_select.return_value = mock_instance
        
        mock_config.num_panels = 2
        await change_panels_wizard(mock_config, "config.yaml")
        
        args, kwargs = mock_select.call_args
        choices = kwargs["choices"]
        default = kwargs["default"]
        
        assert isinstance(choices[0], str)
        assert default == "2"

@pytest.mark.asyncio
async def test_threshold_wizard(mock_config):
    """Verify threshold wizard handles input correctly."""
    with patch("questionary.text") as mock_text, \
         patch.object(TransitConfig, "save") as mock_save:
        mock_instance = MagicMock()
        mock_instance.ask_async = AsyncMock(return_value="10")
        mock_text.return_value = mock_instance
        
        await change_threshold_wizard(mock_config, "config.yaml")
        assert mock_config.arrival_threshold_minutes == 10
        
        args, kwargs = mock_text.call_args
        assert kwargs["default"] == "5"

@pytest.mark.asyncio
async def test_ntfy_wizard(mock_config):
    """Verify ntfy wizard handles input correctly."""
    with patch("questionary.text") as mock_text, \
         patch.object(TransitConfig, "save") as mock_save:
        mock_instance = MagicMock()
        mock_instance.ask_async = AsyncMock(return_value="new-topic")
        mock_text.return_value = mock_instance
        
        await change_ntfy_wizard(mock_config, "config.yaml")
        assert mock_config.ntfy_topic == "new-topic"
        
        args, kwargs = mock_text.call_args
        assert kwargs["default"] == "test-topic"
