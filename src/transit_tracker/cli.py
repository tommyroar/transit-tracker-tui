import argparse
import asyncio
import os
import sys
import time
import subprocess

from .config import TransitConfig, get_last_config_path
from .network.websocket_server import run_server
from .network.websocket_service import run_service as run_client
from .tui import run_cli

PLIST_NAME = "org.eastsideurbanism.transit-tracker.plist"
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_NAME}")

def get_service_status():
    """Returns True if the service is running via launchctl."""
    label = PLIST_NAME.replace(".plist", "")
    res = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
    return res.returncode == 0

def manage_service(action: str):
    label = PLIST_NAME.replace(".plist", "")
    
    if action == "start":
        if get_service_status():
            print(f"[yellow]Service {label} is already running.[/yellow]")
            return
            
        if sys.platform == "darwin":
            if os.path.exists(PLIST_PATH):
                print(f"Starting {label} via launchctl...")
                os.system(f"launchctl load {PLIST_PATH}")
            else:
                print(f"[red]Error: {PLIST_PATH} not found. Run 'transit-tracker install' first.[/red]")
        else:
            print("[red]Service management is only supported on macOS.[/red]")
            
    elif action == "stop":
        if sys.platform == "darwin":
            print(f"Stopping {label} via launchctl...")
            os.system(f"launchctl unload {PLIST_PATH}")
            # Also pkill any lingering GUI or service processes just in case
            subprocess.run(["pkill", "-f", "transit-tracker gui"], capture_output=True)
            subprocess.run(["pkill", "-f", "transit-tracker service"], capture_output=True)
            print("[green]Service stopped.[/green]")
        else:
            print("[red]Service management is only supported on macOS.[/red]")
            
    elif action == "restart":
        manage_service("stop")
        time.sleep(1)
        manage_service("start")
        
    elif action == "status":
        if get_service_status():
            print(f"● {label} is [bold green]running[/bold green]")
        else:
            print(f"○ {label} is [red]stopped[/red]")

async def run_full_service():
    """Runs both the WebSocket server (for HW) and the notification client."""
    path = get_last_config_path()
    if path and os.path.exists(path):
        print(f"[SERVICE] Loading config from {path}")
        config = TransitConfig.load(path)
    else:
        config = TransitConfig.load()
        
    tasks = []
    gui_proc = None
    
    # GUI is tied to service lifecycle
    if sys.platform == "darwin" and config.auto_launch_gui:
        print("[SERVICE] Starting GUI tray icon...")
        gui_proc = subprocess.Popen([sys.executable, "-m", "transit_tracker.cli", "gui"])

    # Always start the local proxy server (for hardware/monitors)
    tasks.append(run_server(config=config))

    if config.use_local_api:
        # Force notification client to use the local server we just started
        config.api_url = "ws://localhost:8000"
    else:
        # If using public API, ensure it's not pointing to localhost
        if "localhost" in config.api_url or "127.0.0.1" in config.api_url:
            config.api_url = "wss://tt.horner.tj/"
    
    # Notification client connects to configured target (Cloud or Local)
    tasks.append(run_client(config=config))
    
    try:
        await asyncio.gather(*tasks)
    finally:
        if gui_proc:
            print("[SERVICE] Cleaning up GUI tray icon...")
            gui_proc.terminate()
            try:
                gui_proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                gui_proc.kill()

def main():
    # Use saved config path if available
    path = get_last_config_path()
    if path and os.path.exists(path):
        config = TransitConfig.load(path)
    else:
        config = TransitConfig.load()

    parser = argparse.ArgumentParser(description="Transit Tracker Configuration")
    parser.add_argument(
        "command",
        nargs="*",
        help="Command to run: 'ui' (default), 'service [start|stop|restart|status]', 'simulator', 'gui', 'web'."
    )

    args = parser.parse_args()
    
    cmd_list = args.command
    primary_cmd = cmd_list[0] if cmd_list else "ui"
    sub_cmd = cmd_list[1] if len(cmd_list) > 1 else None

    if primary_cmd == "service":
        if sub_cmd in ["start", "stop", "restart", "status"]:
            manage_service(sub_cmd)
        else:
            # This is the actual long-running service process
            try:
                asyncio.run(run_full_service())
            except KeyboardInterrupt:
                pass
    elif primary_cmd == "gui":
        from .gui import main as run_gui
        run_gui()
    elif primary_cmd == "simulator":
        from .simulator import run_simulator
        run_simulator(config, force_live=True)
    elif primary_cmd == "web":
        from .web import run_web
        asyncio.run(run_web(config))
    elif primary_cmd == "ui":
        run_cli()
    else:
        # Default fallback
        run_cli()

if __name__ == "__main__":
    main()
