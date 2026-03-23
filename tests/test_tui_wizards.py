from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from transit_tracker.config import TransitConfig
from transit_tracker.tui import (
    change_panels_wizard,
    change_threshold_wizard,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_config():
    config = TransitConfig()
    config.service.use_local_api = True
    config.service.arrival_threshold_minutes = 5
    config.service.num_panels = 2
    return config


@pytest.mark.asyncio
async def test_panels_wizard_choices(mock_config):
    """Verify that change_panels_wizard uses correct default value format."""
    with (
        patch("questionary.select") as mock_select,
        patch.object(TransitConfig, "save") as mock_save,
        patch("transit_tracker.tui.save_service_settings"),
    ):
        mock_instance = MagicMock()
        mock_instance.ask_async = AsyncMock(return_value="3")
        mock_select.return_value = mock_instance

        mock_config.service.num_panels = 2
        mock_console = MagicMock()
        await change_panels_wizard(mock_config, "config.yaml", mock_console)

        args, kwargs = mock_select.call_args
        choices = kwargs["choices"]
        default = kwargs["default"]

        assert isinstance(choices[0], str)
        assert default == "2"


@pytest.mark.asyncio
async def test_threshold_wizard(mock_config):
    """Verify threshold wizard handles input correctly."""
    with (
        patch("questionary.text") as mock_text,
        patch.object(TransitConfig, "save") as mock_save,
        patch("transit_tracker.tui.save_service_settings"),
    ):
        mock_instance = MagicMock()
        mock_instance.ask_async = AsyncMock(return_value="10")
        mock_text.return_value = mock_instance

        await change_threshold_wizard(mock_config, "config.yaml")
        assert mock_config.service.arrival_threshold_minutes == 10

        args, kwargs = mock_text.call_args
        assert kwargs["default"] == "5"
