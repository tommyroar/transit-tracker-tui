import sys
import argparse
import asyncio
import os
from .tui import run_cli
from .network.websocket_service import run_service as run_client
from .network.websocket_server import run_server

from .config import TransitConfig, get_last_config_path

async def run_full_service():
    """Runs both the WebSocket server (for HW) and the notification client."""
    # Priority: 
    # 1. Last used config from global settings
    # 2. Default load logic (config.yaml, .local/config.yaml)
    path = get_last_config_path()
    if path and os.path.exists(path):
        print(f"[SERVICE] Loading config from {path}")
        config = TransitConfig.load(path)
    else:
        config = TransitConfig.load()
        
    tasks = []
    
    if config.use_local_api:
        print("[SERVICE] Mode: Local API (Starting internal server)")
        # Force client to use local server
        config.api_url = "ws://localhost:8000"
        tasks.append(run_server(config=config))
    else:
        # If using public API, ensure it's not pointing to localhost
        if "localhost" in config.api_url or "127.0.0.1" in config.api_url:
            config.api_url = "wss://tt.horner.tj/"
        print(f"[SERVICE] Mode: Public API ({config.api_url})")
    
    tasks.append(run_client(config=config))
    
    print(f"[SERVICE] Starting all background tasks...")
    await asyncio.gather(*tasks)

def main():
    parser = argparse.ArgumentParser(description="Transit Tracker Configuration")
    parser.add_argument(
        "command", 
        nargs="?", 
        choices=["ui", "service", "simulator"], 
        default="ui",
        help="Command to run: 'ui' (default) opens the interactive configuration wizard, 'service' runs the background monitor and server, 'simulator' runs the LED matrix simulator."
    )

    args = parser.parse_args()

    if args.command == "service":
        try:
            asyncio.run(run_full_service())
        except KeyboardInterrupt:
            print("\n[SERVICE] Shutting down...")
    elif args.command == "simulator":
        from .simulator import run_simulator
        from .config import TransitConfig
        config = TransitConfig.load()
        run_simulator(config, force_live=True)
    else:
        run_cli()

if __name__ == "__main__":
    main()
