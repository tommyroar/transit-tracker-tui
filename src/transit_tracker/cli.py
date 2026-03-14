import argparse
import asyncio
import os
import sys
import time
import subprocess

from .config import TransitConfig, get_last_config_path
from .network.websocket_server import run_server
from .network.websocket_service import run_service as run_client
from .tui import PLIST_NAME, PLIST_PATH, run_cli


def get_service_status():
    """Returns True if the service is currently running."""
    # 1. Check for the process directly via pgrep (works on macOS and Linux)
    res = subprocess.run(["pgrep", "-f", "transit-tracker service"], capture_output=True, text=True)
    if res.returncode == 0:
        pids = res.stdout.strip().split("\n")
        # Filter out our own PID if we're currently running a "status" check.
        current_pid = str(os.getpid())
        active_pids = [pid for pid in pids if pid != current_pid]
        if active_pids:
            return True

    if sys.platform == "darwin":
        # 2. Check launchctl as a backup/source of truth for managed services
        label = PLIST_NAME.replace(".plist", "")
        res_launch = os.system(f"launchctl list {label} > /dev/null 2>&1")
        if res_launch == 0:
            return True
            
    return False

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
            print("[bold yellow]Service is already running.[/bold yellow]")
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
            subprocess.Popen([sys.executable, "-m", "transit_tracker.cli", "service"], start_new_session=True)
            print("[green]Service started in background.[/green]")

    elif action == "stop":
        if not get_service_status():
            print("Service is not running.")
            return
        if sys.platform == "darwin":
            print(f"Stopping {label} via launchctl...")
            os.system(f"launchctl unload {PLIST_PATH} > /dev/null 2>&1")
            # Also clean up any GUI processes just in case
            subprocess.run(["pkill", "-f", "transit-tracker gui"], capture_output=True)
            print("[green]Service stopped.[/green]")
        else:
            os.system("pkill -f 'transit-tracker service'")
            os.system("pkill -f 'transit-tracker gui'")
            print("[green]Service stopped.[/green]")

    elif action == "restart":
        manage_service("stop")
        time.sleep(1)
        manage_service("start")

async def run_full_service():
    """Runs both the WebSocket server (for HW) and the notification client."""
    path = get_last_config_path()
    if path and os.path.exists(path):
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
