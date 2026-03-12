import asyncio
import json
import pytest
import websockets
from unittest.mock import AsyncMock, patch, MagicMock
from transit_tracker.network.websocket_server import run_server, TransitServer
from transit_tracker.config import TransitConfig, TransitStop

@pytest.fixture
def mock_config():
    config = TransitConfig()
    config.transit_tracker.stops = [
        TransitStop(stop_id="st:1_8494", routes=["st:40_100240"])
    ]
    # Sync internal state
    config.sync_internal_state()
    return config

@pytest.mark.asyncio
async def test_server_startup_and_connection(mock_config):
    """Test that the server can start and accept connections."""
    server_task = asyncio.create_task(run_server(host="127.0.0.1", port=8765, config=mock_config))
    
    # Wait for server to start
    await asyncio.sleep(0.5)
    
    try:
        async with websockets.connect("ws://127.0.0.1:8765") as ws:
            # Confirm connection works
            assert ws.protocol is not None
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass

@pytest.mark.asyncio
async def test_server_subscription_formats(mock_config):
    """Test that the server handles both TJ Horner and custom subscription formats."""
    server = TransitServer(mock_config)
    server.api = AsyncMock()
    server.api.get_arrivals.return_value = []
    
    # Format 1: TJ Horner routeStopPairs
    msg1 = json.dumps({
        "event": "schedule:subscribe",
        "data": {"routeStopPairs": "st:40_100240,st:1_8494"}
    })
    
    # Format 2: Custom payload
    msg2 = json.dumps({
        "type": "schedule:subscribe",
        "payload": {"routeId": "st:40_100240", "stopId": "st:1_8494"}
    })

    # We override send_update to just capture the subscriptions before the socket closes
    captured_subs = []
    async def mock_send_update(ws):
        captured_subs.append(server.subscriptions.get(ws, []))
        
    server.send_update = mock_send_update
    
    ws1 = AsyncMock()
    ws1.remote_address = ("127.0.0.1", 12345)
    ws1.__aiter__.return_value = [msg1]
    
    await server.register(ws1)
    assert len(captured_subs) == 1
    assert captured_subs[0] == [{"routeId": "st:40_100240", "stopId": "st:1_8494"}]

@pytest.mark.asyncio
async def test_server_broadcast_updates(mock_config):
    """Test that the server broadcasts updates to subscribed clients."""
    server = TransitServer(mock_config)
    server.api = AsyncMock()
    
    mock_arrivals = [
        {
            "tripId": "trip_123",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "arrivalTime": "2026-03-11T12:00:00Z"
        }
    ]
    server.api.get_arrivals.return_value = mock_arrivals
    
    ws = AsyncMock()
    ws.send = AsyncMock()
    server.subscriptions[ws] = [{"routeId": "st:40_100240", "stopId": "st:1_8494"}]
    
    await server.send_update(ws)
    
    ws.send.assert_called_once()
    sent_data = json.loads(ws.send.call_args[0][0])
    assert sent_data["event"] == "schedule"
    assert sent_data["data"]["trips"][0]["tripId"] == "trip_123"

@pytest.mark.asyncio
async def test_full_service_loop(mock_config):
    """Test the full loop using the service script logic."""
    from transit_tracker.network.websocket_service import run_service
    
    port = 8766
    server_task = asyncio.create_task(run_server(host="127.0.0.1", port=port, config=mock_config))
    await asyncio.sleep(0.5)
    
    mock_config.api_url = f"ws://127.0.0.1:{port}"
    mock_config.check_interval_seconds = 1
    
    service_task = asyncio.create_task(run_service(config=mock_config))
    
    await asyncio.sleep(1.0)
    
    service_task.cancel()
    server_task.cancel()
    try:
        await asyncio.gather(service_task, server_task, return_exceptions=True)
    except Exception:
        pass
