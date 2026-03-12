import pytest
import asyncio
from unittest.mock import MagicMock, patch, AsyncMock
from transit_tracker.tui import ask_with_live_dashboard
from transit_tracker.config import TransitConfig

@pytest.mark.asyncio
async def test_live_dashboard_refresh():
    """Verify that ask_with_live_dashboard cancels and recreates prompt on state change."""
    
    config = TransitConfig()
    mock_console = MagicMock()
    
    with patch("transit_tracker.tui.get_dashboard_state") as mock_state, \
         patch("transit_tracker.tui.make_dashboard") as mock_dash, \
         patch("transit_tracker.tui.rprint") as mock_rprint, \
         patch("questionary.select") as mock_select:
         
        # Simulate state changing after 1 second
        # First call gets initial state
        # Then monitor polls. First poll returns same state. Second poll returns new state.
        # Third poll returns same new state... wait, ask_async will be cancelled and re-created.
        
        # We need a side_effect for get_dashboard_state that changes over time
        state_calls = [0]
        def side_effect_state(*args, **kwargs):
            state_calls[0] += 1
            if state_calls[0] <= 2:
                return ("STATE_1",)
            else:
                return ("STATE_2",)
                
        mock_state.side_effect = side_effect_state
        
        # mock_select.return_value.ask_async needs to block so the monitor can cancel it
        # then on the second invocation, it should return a value to break the loop
        
        # Create a mock that blocks until cancelled the first time, then returns a value the second time
        call_count = [0]
        async def mock_ask_async():
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: wait indefinitely (it will be cancelled by the monitor)
                await asyncio.sleep(10)
                return "Should not reach here"
            else:
                # Second call: return a choice to exit the loop
                return "Test Choice"
                
        mock_select.return_value.ask_async = mock_ask_async
        
        result = await ask_with_live_dashboard(
            "Title", ["Test Choice"], config, "config.yaml", mock_console
        )
        
        assert result == "Test Choice"
        assert call_count[0] == 2
        # Verify dashboard was re-rendered (clear + rprint called multiple times)
        assert mock_console.clear.call_count >= 2
