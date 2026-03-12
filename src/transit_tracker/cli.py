import sys
import argparse
import asyncio
import os
import json
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
        config.api_url = "ws://Tommys-Mac-mini.local:8000"
        tasks.append(run_server(config=config))
    else:
        # If using public API, ensure it's not pointing to localhost
        if "localhost" in config.api_url or "127.0.0.1" in config.api_url:
            config.api_url = "wss://tt.horner.tj/"
        print(f"[SERVICE] Mode: Public API ({config.api_url})")
    
    tasks.append(run_client(config=config))
    
    print(f"[SERVICE] Starting all background tasks...")
    await asyncio.gather(*tasks)

def start_gui_if_needed(config: TransitConfig):
    """Starts the macOS tray icon if enabled and not already running."""
    if sys.platform != "darwin" or not config.auto_launch_gui:
        return
    try:
        import subprocess
        # Check if already running using pgrep (idempotent)
        res = subprocess.run(["pgrep", "-f", "transit-tracker gui"], capture_output=True)
        if res.returncode != 0:
            # Not running, launch it in the background
            subprocess.Popen(
                ["transit-tracker", "gui"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
    except Exception:
        pass

TUI_STATE_FILE = os.path.join(os.path.expanduser("~/.config/transit-tracker"), "tui_state.json")

def update_tui_state(pid: int, action: str = "add"):
    """Manages a list of TUI PIDs in the state file."""
    try:
        os.makedirs(os.path.dirname(TUI_STATE_FILE), exist_ok=True)
        data = {"pids": []}
        if os.path.exists(TUI_STATE_FILE):
            try:
                with open(TUI_STATE_FILE, "r") as f:
                    data = json.load(f)
                    if "pids" not in data: data["pids"] = []
            except Exception:
                pass

        if action == "add":
            if pid not in data["pids"]:
                data["pids"].append(pid)
        elif action == "remove":
            if pid in data["pids"]:
                data["pids"].remove(pid)

        with open(TUI_STATE_FILE, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

def main():
    # Load config early to check auto_launch_gui
    path = get_last_config_path()
    if path and os.path.exists(path):
        config = TransitConfig.load(path)
    else:
        config = TransitConfig.load()

    parser = argparse.ArgumentParser(description="Transit Tracker Configuration")
    parser.add_argument(
        "command", 
        nargs="?", 
        choices=["ui", "service", "simulator", "gui"], 
        default="ui",
        help="Command to run: 'ui' (default) opens the interactive configuration wizard, 'service' runs the background monitor and server, 'simulator' runs the LED matrix simulator, 'gui' runs the macOS status bar app."
    )

    args = parser.parse_args()

    # Launch GUI unless specifically running the GUI already or disabled
    if args.command != "gui":
        start_gui_if_needed(config)

    if args.command == "service":
        try:
            asyncio.run(run_full_service())
        except KeyboardInterrupt:
            print("\n[SERVICE] Down...")
    elif args.command == "gui":
        from .gui import main as run_gui
        run_gui()
    elif args.command == "simulator":
        from .simulator import run_simulator
        config = TransitConfig.load()
        run_simulator(config, force_live=True)
    else:
        # We are running 'ui' (TUI)
        my_pid = os.getpid()
        update_tui_state(my_pid, "add")
        try:
            run_cli()
        finally:
            update_tui_state(my_pid, "remove")

if __name__ == "__main__":
    main()
