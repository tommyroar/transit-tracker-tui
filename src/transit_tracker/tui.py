import os
import sys
import json
import asyncio
import time
import threading
import questionary
import difflib
import yaml
from typing import Dict, Any, List
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.live import Live
from rich.syntax import Syntax
from rich import print as rprint
from .config import TransitConfig, TransitSubscription, get_last_config_path, set_last_config_path
from .transit_api import TransitAPI
from .hardware import list_serial_ports, flash_hardware, load_hardware_config, get_usb_devices, is_bootstrapped, flash_base_firmware
from .simulator import run_simulator, async_run_simulator

SERVICE_STATE_FILE = os.path.join(os.path.expanduser("~/.config/transit-tracker"), "service_state.json")

def view_config_diff(config: TransitConfig, config_path: str, console: Console):
    """Shows a diff between in-memory config and on-disk config."""
    if not config_path or not os.path.exists(config_path):
        rprint("[red]No config file on disk to compare with.[/red]")
        time.sleep(2)
        return

    try:
        with open(config_path, "r") as f:
            disk_content = f.read()
        
        # Dump current in-memory config to YAML
        mem_content = yaml.safe_dump(config.model_dump(exclude_unset=True), sort_keys=False)
        
        diff = list(difflib.unified_diff(
            disk_content.splitlines(keepends=True),
            mem_content.splitlines(keepends=True),
            fromfile=f"disk: {os.path.basename(config_path)}",
            tofile="in-memory"
        ))
        
        if not diff:
            rprint("[green]No differences detected between in-memory and disk.[/green]")
        else:
            diff_text = "".join(diff)
            console.print(Panel(Syntax(diff_text, "diff", theme="monokai"), title="Config Diff"))
        
        input("\nPress Enter to continue...")
    except Exception as e:
        rprint(f"[red]Error generating diff: {e}[/red]")
        time.sleep(2)

def view_service_logs(console: Console):
    """Shows the last 50 lines of the service log."""
    log_path = os.path.abspath("service.log")
    if not os.path.exists(log_path):
        rprint(f"[red]Log file not found at {log_path}[/red]")
        time.sleep(2)
        return

    try:
        with open(log_path, "r") as f:
            lines = f.readlines()
            last_lines = "".join(lines[-50:])
        
        console.print(Panel(Text(last_lines), title=f"Service Logs (last 50 lines) - {log_path}"))
        input("\nPress Enter to continue...")
    except Exception as e:
        rprint(f"[red]Error reading logs: {e}[/red]")
        time.sleep(2)

def get_service_state() -> Dict[str, Any]:
    if os.path.exists(SERVICE_STATE_FILE):
        try:
            with open(SERVICE_STATE_FILE, "r") as f:
                state = json.load(f)
                return state
        except Exception as e:
            pass
    return {}

def get_last_service_update() -> str:
    state = get_service_state()
    ts = state.get("last_update")
    if ts:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
    return "Never"

def pick_file(mode="load", default_path=None):
    """Opens a native file chooser dialog."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        
        root = tk.Tk()
        root.withdraw() # Hide the main window
        root.attributes("-topmost", True) # Bring to front
        
        initial_dir = os.path.dirname(default_path) if default_path else os.getcwd()
        
        if mode == "load":
            file_path = filedialog.askopenfilename(
                title="Select Transit Tracker Config",
                initialdir=initial_dir,
                filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")]
            )
        else:
            file_path = filedialog.asksaveasfilename(
                title="Save Transit Tracker Config",
                initialdir=initial_dir,
                defaultextension=".yaml",
                filetypes=[("YAML files", "*.yaml"), ("All files", "*.*")]
            )
            
        root.destroy()
        return file_path if file_path else None
    except Exception as e:
        print(f"Error opening file picker: {e}")
        return None

PLIST_NAME = "org.eastsideurbanism.transit-tracker.plist"
PLIST_PATH = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_NAME}")

def check_service_status():
    if sys.platform != "darwin":
        return "UNSUPPORTED"
    
    # Check 1: Official macOS LaunchAgent
    res = os.system(f"launchctl list {PLIST_NAME.replace('.plist', '')} > /dev/null 2>&1")
    if res == 0:
        return "RUNNING (MANAGED)"
    
    # Check 2: Manual background process (pgrep -f "transit-tracker service")
    try:
        import subprocess
        proc = subprocess.run(
            ["pgrep", "-f", "transit-tracker service"], 
            capture_output=True, 
            text=True
        )
        if proc.returncode == 0 and proc.stdout.strip():
            pids = proc.stdout.strip().split("\n")
            if len(pids) > 0:
                return "RUNNING (MANUAL)"
    except Exception:
        pass

    return "STOPPED"

async def manage_service_menu(config: TransitConfig, config_path: str, console: Console):
    while True:
        status = check_service_status()
        if status == "UNSUPPORTED":
            rprint("[bold red]Background service management is only supported on macOS.[/bold red]")
            break
            
        action = await ask_with_live_dashboard(
            "Service Manager",
            choices=[
                "Start Service" if "RUNNING" not in status else "Stop Service",
                questionary.Choice("Restart Service", disabled="Service not running" if "RUNNING" not in status else None),
                "View Logs",
                "Back"
            ],
            config=config,
            config_path=config_path,
            console=console
        )
        
        if not action or action == "Back":
            break
            
        if action == "View Logs":
            view_service_logs(console)
        elif action == "Restart Service":
            if status == "RUNNING (MANUAL)":
                os.system("pkill -f 'transit-tracker service'")
                rprint("[yellow]Manual service stopped. Restarting...[/yellow]")
                time.sleep(1)
                os.system(f"{sys.executable} -m transit_tracker.cli service &")
                rprint("[green]Manual service restarted.[/green]")
            else:
                os.system(f"launchctl unload {PLIST_PATH} > /dev/null 2>&1")
                time.sleep(1)
                os.system(f"launchctl load {PLIST_PATH}")
                rprint("[green]Managed service restarted.[/green]")
            time.sleep(1)
        elif action == "Start Service":
            python_bin_dir = os.path.dirname(sys.executable)
            transit_tracker_bin = os.path.join(python_bin_dir, "transit-tracker")
            
            if os.path.exists(transit_tracker_bin):
                args_block = f"<string>{transit_tracker_bin}</string>"
            else:
                args_block = f"<string>{sys.executable}</string>\n        <string>-m</string>\n        <string>transit_tracker.cli</string>"

            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_NAME.replace('.plist', '')}</string>
    <key>ProgramArguments</key>
    <array>
        {args_block}
        <string>service</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{os.path.abspath('service.log')}</string>
    <key>StandardErrorPath</key>
    <string>{os.path.abspath('service.log')}</string>
    <key>ProcessType</key>
    <string>Interactive</string>
</dict>
</plist>"""
            with open(PLIST_PATH, "w") as f:
                f.write(plist_content)
            
            # Unload first to be safe, then load
            os.system(f"launchctl unload {PLIST_PATH} > /dev/null 2>&1")
            os.system(f"launchctl load {PLIST_PATH}")
            rprint("[green]Service start requested.[/green]")
            time.sleep(1) # Give launchd a moment to register
            
        elif action == "Stop Service":
            if status == "RUNNING (MANUAL)":
                os.system("pkill -f 'transit-tracker service'")
                rprint("[yellow]Manual service stopped.[/yellow]")
            else:
                os.system(f"launchctl unload {PLIST_PATH}")
                rprint("[yellow]Managed service stopped.[/yellow]")
            time.sleep(1)

async def add_stop_wizard(config: TransitConfig, config_path: str):
    api = TransitAPI()
    try:
        location_query = await questionary.text("Enter your location (cross streets or address):").ask_async()
        if not location_query:
            return

        print("Searching...")
        res = await api.geocode(location_query)
        if not res:
            print("Location not found.")
            return

        lat, lon, display_name = res
        print(f"Found: {display_name}")

        print("Finding nearby routes...")
        routes = await api.get_routes_for_location(lat, lon)
        if not routes:
            print("No transit routes found nearby.")
            return

        route_choices = [
            questionary.Choice(title=f"{r.get('shortName')} - {r.get('description')}", value=r)
            for r in routes
        ]
        
        selected_route = await questionary.select(
            "Select a route:",
            choices=route_choices
        ).ask_async()

        if not selected_route:
            return

        print("Loading stops...")
        stops = await api.get_stops_for_route(selected_route["id"])
        if not stops:
            print("No stops found for this route.")
            return

        stop_choices = [
            questionary.Choice(title=f"{s['name']} ({s['direction_name']})", value=s)
            for s in stops
        ]

        selected_stop = await questionary.select(
            "Select a stop:",
            choices=stop_choices
        ).ask_async()

        if not selected_stop:
            return

        route_id = selected_route["id"]
        agency_id = route_id.split("_")[0]
        feed = "st" if agency_id == "40" else "kcm" if agency_id == "1" else "kcm"
        
        new_sub = TransitSubscription(
            feed=feed,
            route=route_id,
            stop=selected_stop["id"],
            label=f"{selected_route['shortName']} - {selected_stop['name']}"
        )
        
        config.subscriptions.append(new_sub)
        config.save(config_path)
        print(f"\nAdded and saved: {new_sub.label}")

    finally:
        await api.close()

async def remove_stop_wizard(config: TransitConfig, config_path: str):
    if not config.subscriptions:
        print("No stops configured.")
        return

    choices = [
        questionary.Choice(title=f"{sub.label} (Route {sub.route}, Stop {sub.stop})", value=idx)
        for idx, sub in enumerate(config.subscriptions)
    ]
    
    selected_idx = await questionary.select(
        "Select a stop to remove:",
        choices=choices
    ).ask_async()

    if selected_idx is not None:
        removed = config.subscriptions.pop(selected_idx)
        config.save(config_path)
        print(f"Removed: {removed.label}")

async def change_threshold_wizard(config: TransitConfig, config_path: str):
    val = await questionary.text(
        "Enter new alert threshold in minutes:",
        default=str(config.arrival_threshold_minutes)
    ).ask_async()
    
    if val and val.isdigit() and int(val) > 0:
        config.arrival_threshold_minutes = int(val)
        config.save(config_path)
        print("Threshold updated.")
    else:
        print("Invalid input.")

async def change_panels_wizard(config: TransitConfig, config_path: str, console: Console):
    val = await ask_with_live_dashboard(
        "Select number of chained LED panels:",
        choices=["1", "2", "3", "4"],
        config=config,
        config_path=config_path,
        console=console,
        default=str(config.num_panels)
    )
    
    if val:
        config.num_panels = int(val)
        config.save(config_path)
        rprint(f"[green]Hardware setup updated to {val} panel(s).[/green]")

async def change_ntfy_wizard(config: TransitConfig, config_path: str):
    val = await questionary.text(
        "Enter ntfy.sh topic:",
        default=config.ntfy_topic or "transit-alerts"
    ).ask_async()
    
    if val:
        config.ntfy_topic = val
        if config_path:
            config.save(config_path)
            print(f"ntfy.sh topic updated to {val} and saved.")
        else:
            print(f"ntfy.sh topic updated to {val} (in-memory).")

async def change_api_mode_wizard(config: TransitConfig, config_path: str, console: Console):
    mode = await ask_with_live_dashboard(
        "Select API Mode:",
        choices=[
            questionary.Choice("Local (Internal Proxy)", value=True),
            questionary.Choice("Cloud (Public Endpoint)", value=False)
        ],
        config=config,
        config_path=config_path,
        console=console,
        default=config.use_local_api
    )
    
    if mode is not None:
        config.use_local_api = mode
        if not mode:
            url = await questionary.text(
                "Enter Public API URL:",
                default=config.api_url if "Tommys-Mac-mini.local" not in config.api_url else "wss://tt.horner.tj/"
            ).ask_async()
            if url:
                config.api_url = url
        else:
            config.api_url = "ws://localhost:8000/"
            
        config.save(config_path)
        rprint(f"[green]API mode updated to {'Local' if mode else 'Cloud'}.[/green]")

def make_dashboard(config: TransitConfig, config_path: str) -> Panel:
    status = check_service_status()
    state = get_service_state()
    
    # Build Dashboard using rich
    table = Table(show_header=True, header_style="bold magenta", expand=True, box=None)
    table.add_column("Label")
    table.add_column("Feed", style="dim")
    table.add_column("Route", style="cyan")
    table.add_column("Stop ID", style="blue")
    table.add_column("Direction", style="yellow")
    
    for sub in config.subscriptions:
        direction_str = str(sub.direction) if sub.direction is not None else "N/A"
        table.add_row(sub.label, sub.feed, sub.route, sub.stop, direction_str)
        
    status_color = "green" if "RUNNING" in status else "red"
    status_icon = f"[{status_color}]● {status}[/{status_color}]"
    
    # Extract service metadata
    pid = state.get("pid", "Unknown")
    uptime = "N/A"
    start_time = state.get("start_time")
    if start_time:
        uptime_seconds = int(time.time() - start_time)
        uptime = str(time.strftime("%H:%M:%S", time.gmtime(uptime_seconds)))
    messages = state.get("messages_processed", 0)

    status_text = Text(f"Service Status: ", style="bold")
    status_text.append(f"{status}", style=f"bold {status_color}")
    if "RUNNING" in status:
        status_text.append(f" (PID: {pid}, Uptime: {uptime}, Msg: {messages})", style="dim")
    
    if config.use_local_api:
        data_source = "Local (OBA Proxy)"
        port = 8000 
        service_info = f"Serving at: ws://Tommys-Mac-mini.local:{port}"
    else:
        data_source = f"Cloud ({config.api_url})"
        service_info = "Service: Notification Client only"

    source_text = Text(f"Data Source: {data_source}", style="cyan")
    info_text = Text(service_info, style="blue")
    panels_text = Text(f"Hardware Setup: {config.num_panels} Panel(s)", style="magenta")
    config_file_text = Text(f"Current Config: {config_path or 'No file loaded (in-memory)'}", style="dim")
    
    last_svc_update = "Never"
    ts = state.get("last_update")
    if ts:
        last_svc_update = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
    update_text = Text(f"Last Proxy Update: {last_svc_update}", style="yellow")

    # Network Connected Devices
    clients = state.get("clients", [])
    client_count = state.get("client_count", 0)
    if client_count > 0:
        names = []
        for c in clients:
            name = c.get("name", "Unknown")
            if name == "Unknown Device":
                name = c["address"].split(":")[0]
            names.append(name)
        client_text = Text(f"Proxy Clients ({client_count}): {', '.join(names)}", style="green")
    else:
        client_text = Text("Proxy Clients: 0", style="dim")

    # USB Connected Devices
    usb_devices = get_usb_devices()
    if usb_devices:
        device_details = []
        for d in usb_devices:
            port_name = os.path.basename(d["port"])
            device_details.append(f"{d['model']} ({port_name})")
        usb_text = Text(f"USB Hardware: {', '.join(device_details)}", style="cyan")
    else:
        usb_text = Text("No USB Hardware detected", style="dim italic")
    
    header_group = Group(
        status_text,
        usb_text,
        client_text,
        panels_text,
    )
    
    config_group = Group(
        config_file_text,
        source_text,
        info_text,
        update_text,
    )

    panel_group = Group(
        Panel(header_group, title="System Status", border_style="dim"),
        Panel(config_group, title="Configuration", border_style="dim"),
        "",
        table if config.subscriptions else Text("No stops configured yet.", style="italic dim")
    )
    
    return Panel(panel_group, title="[bold cyan]Transit Tracker Manager[/bold cyan]", expand=False, border_style="cyan")

def get_dashboard_state(config: TransitConfig, config_path: str):
    state = get_service_state()
    usb_devices = get_usb_devices()
    return (
        check_service_status(),
        state.get("last_update"),
        state.get("client_count", 0),
        tuple(c.get("address") for c in state.get("clients", [])),
        tuple(d["port"] for d in usb_devices),
        config_path,
        len(config.subscriptions)
    )

async def ask_with_live_dashboard(title, choices, config, config_path, console, default=None):
    last_state = get_dashboard_state(config, config_path)
    
    def show_live_ui():
        dashboard = make_dashboard(config, config_path)
        console.clear()
        rprint(dashboard)
        rprint("\n")

    while True:
        show_live_ui()
        q = questionary.select(title, choices=choices, default=default)
        prompt_task = asyncio.create_task(q.ask_async())
        
        async def monitor():
            while True:
                await asyncio.sleep(0.5)
                current_state = get_dashboard_state(config, config_path)
                if current_state != last_state:
                    return current_state
        
        monitor_task = asyncio.create_task(monitor())
        
        done, pending = await asyncio.wait(
            [prompt_task, monitor_task], 
            return_when=asyncio.FIRST_COMPLETED
        )
        
        if prompt_task in done:
            monitor_task.cancel()
            return prompt_task.result()
            
        if monitor_task in done:
            new_state = monitor_task.result()
            prompt_task.cancel()
            try:
                await prompt_task
            except asyncio.CancelledError:
                pass
            last_state = new_state

async def async_main_menu():
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    
    config_path = get_last_config_path()
    if not config_path or not os.path.exists(config_path):
        candidates = [
            os.path.join(project_root, "accurate_config.yaml"),
            os.path.join(project_root, ".local", "accurate_config.yaml"),
            os.path.join(project_root, "config.yaml"),
            os.path.join(project_root, ".local", "config.yaml")
        ]
        config_path = next((c for c in candidates if os.path.exists(c)), None)
            
    if config_path:
        config = TransitConfig.load(config_path)
        set_last_config_path(config_path)
    else:
        config = TransitConfig()
        config_path = os.path.join(project_root, ".local", "config.yaml") if os.path.exists(".local") else "config.yaml"

    console = Console()
    
    while True:
        usb_devices = get_usb_devices()
        ports = [d["port"] for d in usb_devices]
        has_ports = len(ports) > 0
        has_config = config_path is not None

        choices = [
            "Configurator",
            questionary.Choice("Simulator", disabled="Please load/save config first" if not has_config else None),
            "Service Manager",
            "Restart Service" if "RUNNING" in check_service_status() else None,
            "Exit"
        ]
        
        # Filter out None values (e.g. if Restart Service is not shown)
        choices = [c for c in choices if c is not None]

        action = await ask_with_live_dashboard(
            "What would you like to do?",
            choices=choices,
            config=config,
            config_path=config_path,
            console=console
        )
        
        # Clean up None choices
        if action is None: break

        if action == "Restart Service":
            status = check_service_status()
            if status == "RUNNING (MANUAL)":
                os.system("pkill -f 'transit-tracker service'")
                rprint("[yellow]Manual service stopped. Restarting...[/yellow]")
                time.sleep(1)
                os.system(f"{sys.executable} -m transit_tracker.cli service &")
                rprint("[green]Manual service restarted.[/green]")
            else:
                os.system(f"launchctl unload {PLIST_PATH} > /dev/null 2>&1")
                time.sleep(1)
                os.system(f"launchctl load {PLIST_PATH}")
                rprint("[green]Managed service restarted.[/green]")
            time.sleep(1)
            continue

        if action == "Exit":
            break
            
        elif action == "Configurator":
            while True:
                c_action = await ask_with_live_dashboard(
                    "Configurator",
                    choices=[
                        "Config Files",
                        "Device Config",
                        "Notifications",
                        "API Settings",
                        "Manage Stops",
                        "Change Number of Panels",
                        "Debug",
                        "Back"
                    ],
                    config=config,
                    config_path=config_path,
                    console=console
                )
                
                if not c_action or c_action == "Back":
                    break

                if c_action == "API Settings":
                    await change_api_mode_wizard(config, config_path, console)
                    
                if c_action == "Debug":
                    d_action = await ask_with_live_dashboard(
                        "Debug Menu",
                        choices=["Run Mock Simulator", "Back"],
                        config=config,
                        config_path=config_path,
                        console=console
                    )
                    if d_action == "Run Mock Simulator":
                        await async_run_simulator(config, force_live=False)

                elif c_action == "Config Files":
                    f_action = await ask_with_live_dashboard(
                        "Config Files",
                        choices=[
                            "Load Config File (Picker)", 
                            "Load Config File (Manual Path)", 
                            questionary.Choice("Save Config File", disabled="No config file loaded" if not config_path else None),
                            "Save Config File As...", 
                            "View Config Diff",
                            "Back"
                        ],
                        config=config,
                        config_path=config_path,
                        console=console
                    )
                    
                    if f_action == "View Config Diff":
                        view_config_diff(config, config_path, console)
                    elif f_action == "Save Config File":
                        try:
                            TransitConfig.model_validate(config.model_dump())
                            config.save(config_path)
                            TransitConfig.load(config_path)
                            print(f"Successfully saved and validated {config_path}")
                        except Exception as e:
                            print(f"Error saving or validating config: {e}")

                    elif f_action == "Load Config File (Picker)":
                        new_path = pick_file(mode="load", default_path=config_path)
                        if new_path:
                            try:
                                config = TransitConfig.load(new_path)
                                config_path = new_path
                                set_last_config_path(new_path)
                                print(f"Loaded config from {new_path}")
                                has_config = True
                            except Exception as e:
                                print(f"Error loading config: {e}")
                    elif f_action == "Load Config File (Manual Path)":
                        new_path = await questionary.path(
                            "Enter path to load config from:",
                            default=config_path or "config.yaml"
                        ).ask_async()
                        if new_path:
                            try:
                                config = TransitConfig.load(new_path)
                                config_path = new_path
                                set_last_config_path(new_path)
                                print(f"Loaded config from {new_path}")
                                has_config = True
                            except Exception as e:
                                print(f"Error loading config: {e}")
                    elif f_action == "Save Config File As...":
                        new_path = pick_file(mode="save", default_path=config_path)
                        if not new_path:
                            new_path = await questionary.path(
                                "Enter path to save config to (fallback):",
                                default=config_path or "config.yaml"
                            ).ask_async()
                            
                        if new_path:
                            try:
                                TransitConfig.model_validate(config.model_dump())
                                config.save(new_path)
                                TransitConfig.load(new_path)
                                config_path = new_path
                                set_last_config_path(new_path)
                                print(f"Saved and validated config to {new_path}")
                                has_config = True
                            except Exception as e:
                                print(f"Error saving or validating config: {e}")
                                
                elif c_action == "Device Config":
                    d_action = await ask_with_live_dashboard(
                        "Device Config",
                        choices=[
                            questionary.Choice("Flash Device", disabled="No device connected" if not has_ports else None),
                            questionary.Choice("Download from Device", disabled="No device connected" if not has_ports else None),
                            "Back"
                        ],
                        config=config,
                        config_path=config_path,
                        console=console
                    )
                    
                    if d_action == "Flash Device":
                        selected_port = await questionary.select(
                            "Select your Transit Tracker device:",
                            choices=ports
                        ).ask_async()
                        if selected_port:
                            if not is_bootstrapped(selected_port):
                                console.print("[bold yellow]Warning: This device does not appear to have the transit-tracker firmware installed yet.[/bold yellow]")
                                do_install = await questionary.confirm("Do you want to install the base firmware from the official website? (This will erase existing data)").ask_async()
                                if do_install:
                                    success = flash_base_firmware(selected_port)
                                    if success:
                                        console.print("[green]Base firmware installed. Continuing with device configuration...[/green]")
                                        # Give device a moment to reboot
                                        import time
                                        time.sleep(3)
                                        flash_hardware(selected_port, config)
                                    else:
                                        console.print("[red]Base firmware installation failed.[/red]")
                                else:
                                    console.print("Continuing with configuration update anyway...")
                                    flash_hardware(selected_port, config)
                            else:
                                flash_hardware(selected_port, config)
                    elif d_action == "Download from Device":
                        selected_port = await questionary.select(
                            "Select your Transit Tracker device:",
                            choices=ports
                        ).ask_async()
                        if selected_port:
                            if load_hardware_config(selected_port, config):
                                if config_path:
                                    config.save(config_path)
                                    print("Configuration updated and saved to file.")
                                else:
                                    print("Configuration read into memory. Please save it to a file.")

                elif c_action == "Notifications":
                    n_action = await ask_with_live_dashboard(
                        "Notifications",
                        choices=[
                            questionary.Choice("Change Alert Threshold", disabled="Please load or save a config file first" if not has_config else None),
                            "Add/Change ntfy.sh Endpoint",
                            "Back"
                        ],
                        config=config,
                        config_path=config_path,
                        console=console
                    )
                    
                    if n_action == "Change Alert Threshold":
                        await change_threshold_wizard(config, config_path)
                    elif n_action == "Add/Change ntfy.sh Endpoint":
                        await change_ntfy_wizard(config, config_path)
                        
                elif c_action == "Manage Stops":
                    s_action = await ask_with_live_dashboard(
                        "Manage Stops",
                        choices=[
                            questionary.Choice("Add a Stop", disabled="Please load or save a config file first" if not has_config else None),
                            questionary.Choice("Remove a Stop", disabled="Please load or save a config file first" if not has_config else None),
                            "Back"
                        ],
                        config=config,
                        config_path=config_path,
                        console=console
                    )
                    
                    if s_action == "Add a Stop":
                        await add_stop_wizard(config, config_path)
                    elif s_action == "Remove a Stop":
                        await remove_stop_wizard(config, config_path)
                        
                elif c_action == "Change Number of Panels":
                    await change_panels_wizard(config, config_path, console)

        elif action == "Simulator":
            rprint(f"[dim]Using config: {config_path}[/dim]")
            await async_run_simulator(config, force_live=True)

        elif action == "Service Manager":
            await manage_service_menu(config, config_path, console)

def main_menu():
    asyncio.run(async_main_menu())

def hardware_monitor():
    known_ports = {} # port -> model
    devices = get_usb_devices()
    for d in devices:
        known_ports[d["port"]] = d["model"]
        
    while True:
        time.sleep(1)
        current_devices = get_usb_devices()
        current_ports = {d["port"]: d["model"] for d in current_devices}
        
        added = set(current_ports.keys()) - set(known_ports.keys())
        removed = set(known_ports.keys()) - set(current_ports.keys())
        
        if added or removed:
            sys.stdout.write("\r\033[K") 
            for p in added:
                model = current_ports[p]
                rprint(f"[bold green]USB Device Connected:[/bold green] [cyan]{model}[/cyan] at {p}")
            for p in removed:
                model = known_ports[p]
                rprint(f"[bold yellow]USB Device Disconnected:[/bold yellow] [dim]{model}[/dim]")
            known_ports = current_ports
            sys.stdout.flush()

def run_cli():
    # Start the hardware monitor as a daemon thread
    monitor_thread = threading.Thread(target=hardware_monitor, daemon=True)
    monitor_thread.start()

    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nExiting...")
