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
CONTAINER_NAME = "transit-tracker"


def _container_running() -> bool:
    """Check if the transit-tracker Docker container is running."""
    res = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
        capture_output=True, text=True,
    )
    return res.returncode == 0 and "true" in res.stdout.lower()


def get_service_status():
    """Returns True if the service is running (container or launchctl)."""
    if _container_running():
        return True
    label = PLIST_NAME.replace(".plist", "")
    res = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
    return res.returncode == 0


def manage_service(action: str):
    """Manage the transit-tracker service.

    Prefers Docker container management. Falls back to launchctl for
    legacy non-containerised setups.
    """
    # If a container exists (running or stopped), manage via Docker
    res = subprocess.run(
        ["docker", "inspect", CONTAINER_NAME],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        _manage_service_docker(action)
    else:
        _manage_service_launchctl(action)


def _manage_service_docker(action: str):
    if action == "start":
        if _container_running():
            print(f"[yellow]Container {CONTAINER_NAME} is already running.[/yellow]")
            return
        print(f"Starting container {CONTAINER_NAME}...")
        result = subprocess.run(
            ["docker", "start", CONTAINER_NAME],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"[green]Container {CONTAINER_NAME} started.[/green]")
        else:
            print(f"[red]Failed to start: {result.stderr.strip()}[/red]")

    elif action == "stop":
        print(f"Stopping container {CONTAINER_NAME}...")
        subprocess.run(["pkill", "-f", "transit-tracker gui"], capture_output=True)
        result = subprocess.run(
            ["docker", "stop", CONTAINER_NAME],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"[green]Container {CONTAINER_NAME} stopped.[/green]")
        else:
            print(f"[red]Failed to stop: {result.stderr.strip()}[/red]")

    elif action == "restart":
        subprocess.run(["pkill", "-f", "transit-tracker gui"], capture_output=True)
        print(f"Restarting container {CONTAINER_NAME}...")
        result = subprocess.run(
            ["docker", "restart", CONTAINER_NAME],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"[green]Container {CONTAINER_NAME} restarted.[/green]")
        else:
            print(f"[red]Failed to restart: {result.stderr.strip()}[/red]")

    elif action == "status":
        if _container_running():
            # Get uptime from container inspect
            res = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.StartedAt}}", CONTAINER_NAME],
                capture_output=True, text=True,
            )
            started = res.stdout.strip() if res.returncode == 0 else "unknown"
            print(f"● {CONTAINER_NAME} is [bold green]running[/bold green] (since {started})")
        else:
            print(f"○ {CONTAINER_NAME} is [red]stopped[/red]")


def _manage_service_launchctl(action: str):
    label = PLIST_NAME.replace(".plist", "")

    if action == "start":
        if sys.platform != "darwin":
            print("[red]Service management requires macOS.[/red]")
            return
        if get_service_status():
            print(f"[yellow]Service {label} is already running.[/yellow]")
            return
        if os.path.exists(PLIST_PATH):
            print(f"Starting {label} via launchctl...")
            os.system(f"launchctl load {PLIST_PATH}")
        else:
            print(f"[red]Error: {PLIST_PATH} not found. Run 'transit-tracker install' first.[/red]")

    elif action == "stop":
        if sys.platform != "darwin":
            print("[red]Service management requires macOS.[/red]")
            return
        print(f"Stopping {label} via launchctl...")
        os.system(f"launchctl unload {PLIST_PATH}")
        subprocess.run(["pkill", "-f", "transit-tracker gui"], capture_output=True)
        subprocess.run(["pkill", "-f", "transit-tracker service"], capture_output=True)
        print("[green]Service stopped.[/green]")

    elif action == "restart":
        if sys.platform != "darwin":
            manage_service("stop")
            time.sleep(1)
            manage_service("start")
            return
        subprocess.run(["pkill", "-f", "transit-tracker gui"], capture_output=True)
        uid = os.getuid()
        result = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{uid}/{label}"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"Service {label} restarted.")
        else:
            print(f"kickstart failed ({result.stderr.strip()}), falling back to stop/start...")
            _manage_service_launchctl("stop")
            time.sleep(1)
            _manage_service_launchctl("start")

    elif action == "status":
        res = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
        if res.returncode == 0:
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
