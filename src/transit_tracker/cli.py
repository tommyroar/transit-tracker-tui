import sys
import argparse
import asyncio
import os
import json
import time
from .tui import run_cli, PLIST_NAME, PLIST_PATH
from .network.websocket_service import run_service as run_client
from .network.websocket_server import run_server

from .config import TransitConfig, get_last_config_path

def get_service_status():
    """Returns True if the service is currently running."""
    if sys.platform == "darwin":
        # Check launchctl for our label
        label = PLIST_NAME.replace(".plist", "")
        res = os.system(f"launchctl list {label} > /dev/null 2>&1")
        return res == 0
    else:
        # Fallback to pgrep for other platforms
        import subprocess
        res = subprocess.run(["pgrep", "-f", "transit-tracker service"], capture_output=True)
        return res.returncode == 0

def manage_service(action: str):
    """Handles starting, stopping, and status for the background service."""
    label = PLIST_NAME.replace(".plist", "")
    
    if action == "status":
        if get_service_status():
            print(f"● {label} is [bold green]running[/bold green]")
        else:
            print(f"○ {label} is [red]stopped[/red]")
            
    elif action == "start":
        if get_service_status():
            print(f"[bold yellow]Service is already running.[/bold yellow]")
            return
        if sys.platform == "darwin":
            if os.path.exists(PLIST_PATH):
                print(f"Starting {label} via launchctl...")
                os.system(f"launchctl load {PLIST_PATH}")
                print("[green]Service started.[/green]")
            else:
                print(f"[red]Error:[/red] Service plist not found at {PLIST_PATH}. Run 'transit-tracker' first to configure.")
        else:
            # Simple background spawn for Linux/Others
            import subprocess
            subprocess.Popen([sys.executable, "-m", "transit_tracker.cli", "service"], start_new_session=True)
            print("[green]Service started in background.[/green]")

    elif action == "stop":
        if not get_service_status():
            print(f"Service is not running.")
            return
        if sys.platform == "darwin":
            print(f"Stopping {label} via launchctl...")
            os.system(f"launchctl unload {PLIST_PATH} > /dev/null 2>&1")
            print("[green]Service stopped.[/green]")
        else:
            os.system("pkill -f 'transit-tracker service'")
            print("[green]Service stopped.[/green]")

    elif action == "restart":
        manage_service("stop")
        time.sleep(1)
        manage_service("start")

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
            # Find the absolute path to ourselves
            # sys.executable is .../.venv/bin/python
            # transit-tracker is .../.venv/bin/transit-tracker
            python_bin_dir = os.path.dirname(sys.executable)
            transit_tracker_bin = os.path.join(python_bin_dir, "transit-tracker")
            
            # Fallback to just the command if not found in same bin
            cmd = [transit_tracker_bin, "gui"] if os.path.exists(transit_tracker_bin) else ["transit-tracker", "gui"]
            
            # Not running, launch it in the background
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
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
        nargs="*", 
        help="Command to run: 'ui' (default), 'service [start|stop|restart|status]', 'simulator', 'gui'."
    )

    args = parser.parse_args()
    
    cmd_list = args.command
    primary_cmd = cmd_list[0] if cmd_list else "ui"
    sub_cmd = cmd_list[1] if len(cmd_list) > 1 else None

    # The gui is started for any command (except gui itself) to provide visual status.
    if primary_cmd != "gui":
        start_gui_if_needed(config)

    if primary_cmd == "service":
        if sub_cmd in ["start", "stop", "restart", "status"]:
            manage_service(sub_cmd)
        else:
            try:
                asyncio.run(run_full_service())
            except KeyboardInterrupt:
                print("\n[SERVICE] Down...")
    elif primary_cmd == "gui":
        from .gui import main as run_gui
        run_gui()
    elif primary_cmd == "simulator":
        from .simulator import run_simulator
        config = TransitConfig.load()
        run_simulator(config, force_live=True)
    else:
        run_cli()

if __name__ == "__main__":
    main()
