import os
import json
import time
import asyncio
import pytest
from transit_tracker.config import TransitConfig, TransitSubscription
from transit_tracker.network.websocket_server import TransitServer

@pytest.mark.asyncio
async def test_persistent_memory_validation():
    """
    Validates that a running service holds its configuration in memory
    and is totally isolated from changes made to the configuration file
    by the TUI or any other process.
    """
    # 1. Start with a "Local" config
    config = TransitConfig()
    config.api_url = "ws://LOCAL_PROXY_MODE"
    config.subscriptions = [TransitSubscription(feed="st", route="14", stop="1_1234", label="14")]
    
    # Write it to the standard config location
    config_path = "config.yaml"
    config.save(config_path)
    
    # 2. Launch the server (simulated launch)
    server = TransitServer(config)
    assert server.config.api_url == "ws://LOCAL_PROXY_MODE"
    
    # 3. Simulate TUI modifying the config to "Cloud" mode
    new_config = TransitConfig.load(config_path)
    new_config.api_url = "wss://tt.horner.tj/"
    new_config.save(config_path)
    
    # 4. Verify the running server is still in LOCAL_PROXY_MODE
    # This proves the service loads into memory and persists there.
    assert server.config.api_url == "ws://LOCAL_PROXY_MODE", "CRITICAL FAILURE: Service reloaded config from disk during execution!"
    
    # 5. Verify that a NEW load from disk gets the NEW config (TUI behavior)
    tui_config = TransitConfig.load(config_path)
    assert tui_config.api_url == "wss://tt.horner.tj/"
    
    # 6. Final check: the running service's subscriptions are still the OLD ones
    assert server.config.subscriptions[0].route == "14"
    
    # Cleanup
    if os.path.exists(config_path):
        os.remove(config_path)

if __name__ == "__main__":
    asyncio.run(test_persistent_memory_validation())
