import asyncio
import json
import os

import pytest

from transit_tracker.config import TransitConfig
from transit_tracker.network.websocket_server import TransitServer

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_service_isolation_from_disk_changes():
    """
    Validates that once the TransitServer is started with a config object,
    modifications to the configuration files on disk have ZERO effect on 
    the running service's internal state.
    """
    # 1. SETUP INITIAL CONFIG
    config_a = TransitConfig()
    config_a.api_url = "ws://localhost:8000"
    config_a.transit_tracker.base_url = "ws://localhost:8000"
    
    # Save it to a dummy file
    test_config_path = "test_isolation_config.yaml"
    config_a.save(test_config_path)
    
    # 2. START SERVER WITH CONFIG A
    server = TransitServer(config_a)
    
    # Verify initial state
    assert server.config.api_url == "ws://localhost:8000"
    
    # 3. MODIFY DISK CONFIG TO CONFIG B
    with open(test_config_path, "w") as f:
        f.write("api_url: wss://tt.horner.tj/\n")
        f.write("use_local_api: false\n")
        
    # 4. VALIDATE SERVICE STILL HAS CONFIG A IN MEMORY
    # The server should NOT have any file observers or re-load logic
    assert server.config.api_url == "ws://localhost:8000", "Server state changed after disk modification!"
    
    # 5. VALIDATE TUI SESSION ISOLATION
    # If we load a 'new' session, it shouldn't touch the server's instance
    config_b = TransitConfig.load(test_config_path)
    assert config_b.api_url == "wss://tt.horner.tj/"
    assert server.config.api_url == "ws://localhost:8000", "TUI session load affected running server!"
    
    # Cleanup
    if os.path.exists(test_config_path):
        os.remove(test_config_path)

@pytest.mark.asyncio
async def test_sync_state_does_not_mutate_config():
    """
    Ensures that writing the service state to disk (heartbeat) 
    never results in a reload of the configuration.
    """
    config = TransitConfig()
    config.api_url = "ws://LOCAL_TEST"
    server = TransitServer(config)
    
    # Force a sync_state
    server.sync_state()
    
    # Config in memory should be untouched
    assert server.config.api_url == "ws://LOCAL_TEST"
    
    # Verify the state file exists but doesn't overwrite our intention
    from transit_tracker.network.websocket_server import SERVICE_STATE_FILE
    assert os.path.exists(SERVICE_STATE_FILE)
    with open(SERVICE_STATE_FILE, "r") as f:
        state = json.load(f)
        assert state["status"] == "active"

if __name__ == "__main__":
    asyncio.run(test_service_isolation_from_disk_changes())
    asyncio.run(test_sync_state_does_not_mutate_config())
