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
NOMAD_JOB = "transit-tracker"
NOMAD_JOB_FILE = os.path.expanduser(
    "~/dev/transit_tracker/transit-tracker.nomad.hcl"
)


def _nomad_available() -> bool:
    """Check if Nomad agent is reachable."""
    res = subprocess.run(
        ["nomad", "status"], capture_output=True, text=True
    )
    return res.returncode == 0


def _nomad_job_running() -> bool:
    """Check if the transit-tracker Nomad job is running."""
    res = subprocess.run(
        ["nomad", "job", "status", "-short", NOMAD_JOB],
        capture_output=True, text=True,
    )
    return res.returncode == 0 and "running" in res.stdout.lower()


def get_service_status():
    """Returns True if the service is running."""
    if _nomad_available():
        return _nomad_job_running()
    # Fallback to launchctl
    label = PLIST_NAME.replace(".plist", "")
    res = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
    return res.returncode == 0


def manage_service(action: str):
    if _nomad_available():
        _manage_service_nomad(action)
    else:
        _manage_service_launchctl(action)


def _manage_service_nomad(action: str):
    if action == "start":
        if _nomad_job_running():
            print(f"[yellow]Job {NOMAD_JOB} is already running.[/yellow]")
            return
        job_file = os.path.normpath(NOMAD_JOB_FILE)
        if not os.path.exists(job_file):
            print(f"[red]Error: {job_file} not found.[/red]")
            return
        print(f"Starting {NOMAD_JOB} via Nomad...")
        result = subprocess.run(
            ["nomad", "job", "run", job_file],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"[green]Job {NOMAD_JOB} started.[/green]")
        else:
            print(f"[red]Failed to start: {result.stderr.strip()}[/red]")

    elif action == "stop":
        print(f"Stopping {NOMAD_JOB} via Nomad...")
        subprocess.run(["pkill", "-f", "transit-tracker gui"], capture_output=True)
        result = subprocess.run(
            ["nomad", "job", "stop", NOMAD_JOB],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"[green]Job {NOMAD_JOB} stopped.[/green]")
        else:
            print(f"[red]Failed to stop: {result.stderr.strip()}[/red]")

    elif action == "restart":
        subprocess.run(["pkill", "-f", "transit-tracker gui"], capture_output=True)
        job_file = os.path.normpath(NOMAD_JOB_FILE)
        if not os.path.exists(job_file):
            print(f"[red]Error: {job_file} not found.[/red]")
            return
        # Nomad `job run` on an existing job restarts it
        print(f"Restarting {NOMAD_JOB} via Nomad...")
        # Stop first, then start to get a clean restart
        subprocess.run(
            ["nomad", "job", "stop", NOMAD_JOB],
            capture_output=True, text=True,
        )
        time.sleep(1)
        result = subprocess.run(
            ["nomad", "job", "run", job_file],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"Job {NOMAD_JOB} restarted.")
        else:
            print(f"[red]Failed to restart: {result.stderr.strip()}[/red]")

    elif action == "status":
        result = subprocess.run(
            ["nomad", "job", "status", NOMAD_JOB],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and "running" in result.stdout.lower():
            print(f"● {NOMAD_JOB} is [bold green]running[/bold green]")
            # Show allocation summary
            for line in result.stdout.strip().split("\n")[-5:]:
                print(f"  {line}")
        else:
            print(f"○ {NOMAD_JOB} is [red]stopped[/red]")


def _manage_service_launchctl(action: str):
    label = PLIST_NAME.replace(".plist", "")

    if action == "start":
        if sys.platform != "darwin":
            print("[red]Service management requires macOS or Nomad.[/red]")
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
            print("[red]Service management requires macOS or Nomad.[/red]")
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
