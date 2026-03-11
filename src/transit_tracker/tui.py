import os
import sys
import asyncio
import time
import threading
import questionary
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import print as rprint
from .config import TransitConfig, TransitSubscription, get_last_config_path, set_last_config_path
from .transit_api import TransitAPI
from .hardware import list_serial_ports, flash_hardware, load_hardware_config
from .simulator import run_simulator

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
    res = os.system(f"launchctl list {PLIST_NAME.replace('.plist', '')} > /dev/null 2>&1")
    return "RUNNING" if res == 0 else "STOPPED"

def manage_service_menu():
    while True:
        status = check_service_status()
        if status == "UNSUPPORTED":
            print("Background service management is only supported on macOS.")
            break
            
        action = questionary.select(
            f"Manage Service (Status: {status})",
            choices=[
                "Start Service" if status == "STOPPED" else "Stop Service",
                "Back"
            ]
        ).ask()
        
        if not action or action == "Back":
            break
            
        if action == "Start Service":
            python_path = sys.executable
            script_path = os.path.abspath(sys.argv[0])
            plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{PLIST_NAME.replace('.plist', '')}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{python_path}</string>
        <string>-m</string>
        <string>transit_tracker.cli</string>
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
</dict>
</plist>"""
            with open(PLIST_PATH, "w") as f:
                f.write(plist_content)
            os.system(f"launchctl load {PLIST_PATH}")
            print("Service started.")
        elif action == "Stop Service":
            os.system(f"launchctl unload {PLIST_PATH}")
            print("Service stopped.")

async def add_stop_wizard(config: TransitConfig, config_path: str):
    api = TransitAPI()
    try:
        # Step 1: Location Search
        location_query = questionary.text("Enter your location (cross streets or address):").ask()
        if not location_query:
            return

        print("Searching...")
        res = await api.geocode(location_query)
        if not res:
            print("Location not found.")
            return

        lat, lon, display_name = res
        print(f"Found: {display_name}")

        # Step 2: Route Selection
        print("Finding nearby routes...")
        routes = await api.get_routes_for_location(lat, lon)
        if not routes:
            print("No transit routes found nearby.")
            return

        route_choices = [
            questionary.Choice(title=f"{r.get('shortName')} - {r.get('description')}", value=r)
            for r in routes
        ]
        
        selected_route = questionary.select(
            "Select a route:",
            choices=route_choices
        ).ask()

        if not selected_route:
            return

        # Step 3: Stop Selection
        print("Loading stops...")
        stops = await api.get_stops_for_route(selected_route["id"])
        if not stops:
            print("No stops found for this route.")
            return

        stop_choices = [
            questionary.Choice(title=f"{s['name']} ({s['direction_name']})", value=s)
            for s in stops
        ]

        selected_stop = questionary.select(
            "Select a stop:",
            choices=stop_choices
        ).ask()

        if not selected_stop:
            return

        # Step 4: Add to config
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

def remove_stop_wizard(config: TransitConfig, config_path: str):
    if not config.subscriptions:
        print("No stops configured.")
        return

    choices = [
        questionary.Choice(title=f"{sub.label} (Route {sub.route}, Stop {sub.stop})", value=idx)
        for idx, sub in enumerate(config.subscriptions)
    ]
    
    selected_idx = questionary.select(
        "Select a stop to remove:",
        choices=choices
    ).ask()

    if selected_idx is not None:
        removed = config.subscriptions.pop(selected_idx)
        config.save(config_path)
        print(f"Removed: {removed.label}")

def change_threshold_wizard(config: TransitConfig, config_path: str):
    val = questionary.text(
        "Enter new alert threshold in minutes:",
        default=str(config.arrival_threshold_minutes)
    ).ask()
    
    if val and val.isdigit() and int(val) > 0:
        config.arrival_threshold_minutes = int(val)
        config.save(config_path)
        print("Threshold updated.")
    else:
        print("Invalid input.")

def change_panels_wizard(config: TransitConfig, config_path: str):
    val = questionary.select(
        "Select number of chained LED panels:",
        choices=["1", "2", "3", "4"],
        default=str(config.num_panels)
    ).ask()
    
    if val:
        config.num_panels = int(val)
        config.save(config_path)
        print(f"Hardware setup updated to {val} panel(s).")

def change_ntfy_wizard(config: TransitConfig, config_path: str):
    val = questionary.text(
        "Enter ntfy.sh topic:",
        default=config.ntfy_topic or "transit-alerts"
    ).ask()
    
    if val:
        config.ntfy_topic = val
        if config_path:
            config.save(config_path)
            print(f"ntfy.sh topic updated to {val} and saved.")
        else:
            print(f"ntfy.sh topic updated to {val} (in-memory).")

def change_api_mode_wizard(config: TransitConfig, config_path: str):
    mode = questionary.select(
        "Select API Mode:",
        choices=[
            questionary.Choice("Local (Internal Proxy)", value=True),
            questionary.Choice("Cloud (Public Endpoint)", value=False)
        ],
        default="Local (Internal Proxy)" if config.use_local_api else "Cloud (Public Endpoint)"
    ).ask()
    
    if mode is not None:
        config.use_local_api = mode
        if not mode:
            # If switching to cloud, ask for the URL or use default
            url = questionary.text(
                "Enter Public API URL:",
                default=config.api_url if "localhost" not in config.api_url else "wss://tt.horner.tj/"
            ).ask()
            if url:
                config.api_url = url
        else:
            config.api_url = "ws://localhost:8000/"
            
        config.save(config_path)
        print(f"API mode updated to {'Local' if mode else 'Cloud'}.")

def main_menu():
    # Find project root (two levels up from src/transit_tracker)
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    
    # Configuration search order:
    # 1. accurate_config.yaml (root)
    # 2. .local/accurate_config.yaml
    # 3. config.yaml (root)
    # 4. .local/config.yaml
    # 5. Last used path from global settings
    
    candidates = [
        os.path.join(project_root, "accurate_config.yaml"),
        os.path.join(project_root, ".local", "accurate_config.yaml"),
        os.path.join(project_root, "config.yaml"),
        os.path.join(project_root, ".local", "config.yaml")
    ]
    
    config_path = next((c for c in candidates if os.path.exists(c)), None)
    
    if not config_path:
        last_path = get_last_config_path()
        if last_path and os.path.exists(last_path):
            config_path = last_path
            
    if config_path:
        config = TransitConfig.load(config_path)
        set_last_config_path(config_path)
    else:
        config = TransitConfig()
        config_path = os.path.join(project_root, ".local", "config.yaml") if os.path.exists(".local") else "config.yaml"

    console = Console()

    while True:
        status = check_service_status()
        
        # Build Dashboard using rich
        table = Table(show_header=True, header_style="bold magenta", expand=True)
        table.add_column("Label")
        table.add_column("Feed", style="dim")
        table.add_column("Route", style="cyan")
        table.add_column("Stop ID", style="blue")
        table.add_column("Direction", style="yellow")
        
        for sub in config.subscriptions:
            direction_str = str(sub.direction) if sub.direction is not None else "N/A"
            table.add_row(sub.label, sub.feed, sub.route, sub.stop, direction_str)
            
        status_color = "green" if status == "RUNNING" else "red"
        status_text = Text(f"Service Status: {status}", style=f"bold {status_color}")
        
        threshold_text = Text(f"Alert Threshold: {config.arrival_threshold_minutes} minutes", style="yellow")
        panels_text = Text(f"Hardware Setup: {config.num_panels} Panel(s)", style="magenta")
        config_file_text = Text(f"Current Config: {config_path or 'No file loaded (in-memory)'}", style="dim")
        
        ports = list_serial_ports()
        if ports:
            device_text = Text(f"Hardware Detected: {', '.join(ports)}", style="cyan")
        else:
            device_text = Text("No device connected", style="dim italic")
        
        panel_group = Group(
            status_text,
            device_text,
            panels_text,
            config_file_text,
            threshold_text,
            "",
            table if config.subscriptions else Text("No stops configured yet.", style="italic dim")
        )
        
        rprint("\n")
        rprint(Panel(panel_group, title="[bold cyan]Transit Tracker Manager[/bold cyan]", expand=False, border_style="cyan"))
        rprint("\n")

        has_ports = len(ports) > 0
        has_config = config_path is not None

        action = questionary.select(
            "What would you like to do?",
            choices=[
                "Configurator",
                questionary.Choice("Simulator", disabled="Please load or save a config file first" if not has_config else None),
                "Service Manager",
                "Exit"
            ]
        ).ask()

        if not action or action == "Exit":
            break
            
        elif action == "Configurator":
            while True:
                c_action = questionary.select(
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
                    ]
                ).ask()
                
                if not c_action or c_action == "Back":
                    break

                if c_action == "API Settings":
                    change_api_mode_wizard(config, config_path)
                    
                if c_action == "Debug":
                    d_action = questionary.select(
                        "Debug Menu",
                        choices=["Run Mock Simulator", "Back"]
                    ).ask()
                    if d_action == "Run Mock Simulator":
                        run_simulator(config, force_live=False)

                elif c_action == "Config Files":
                    f_action = questionary.select(
                        "Config Files",
                        choices=["Load Config File (Picker)", "Load Config File (Manual Path)", "Save Config File As...", "Back"]
                    ).ask()
                    
                    if f_action == "Load Config File (Picker)":
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
                        new_path = questionary.path(
                            "Enter path to load config from:",
                            default=config_path or "config.yaml"
                        ).ask()
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
                            new_path = questionary.path(
                                "Enter path to save config to (fallback):",
                                default=config_path or "config.yaml"
                            ).ask()
                            
                        if new_path:
                            try:
                                config.save(new_path)
                                config_path = new_path
                                set_last_config_path(new_path)
                                print(f"Saved config to {new_path}")
                                has_config = True
                            except Exception as e:
                                print(f"Error saving config: {e}")
                                
                elif c_action == "Device Config":
                    d_action = questionary.select(
                        "Device Config",
                        choices=[
                            questionary.Choice("Flash Device", disabled="No device connected" if not has_ports else None),
                            questionary.Choice("Download from Device", disabled="No device connected" if not has_ports else None),
                            "Back"
                        ]
                    ).ask()
                    
                    if d_action == "Flash Device":
                        selected_port = questionary.select(
                            "Select your Transit Tracker device:",
                            choices=ports
                        ).ask()
                        if selected_port:
                            flash_hardware(selected_port, config)
                    elif d_action == "Download from Device":
                        selected_port = questionary.select(
                            "Select your Transit Tracker device:",
                            choices=ports
                        ).ask()
                        if selected_port:
                            if load_hardware_config(selected_port, config):
                                if config_path:
                                    config.save(config_path)
                                    print("Configuration updated and saved to file.")
                                else:
                                    print("Configuration read into memory. Please save it to a file.")

                elif c_action == "Notifications":
                    n_action = questionary.select(
                        "Notifications",
                        choices=[
                            questionary.Choice("Change Alert Threshold", disabled="Please load or save a config file first" if not has_config else None),
                            "Add/Change ntfy.sh Endpoint",
                            "Back"
                        ]
                    ).ask()
                    
                    if n_action == "Change Alert Threshold":
                        change_threshold_wizard(config, config_path)
                    elif n_action == "Add/Change ntfy.sh Endpoint":
                        change_ntfy_wizard(config, config_path)
                        
                elif c_action == "Manage Stops":
                    s_action = questionary.select(
                        "Manage Stops",
                        choices=[
                            questionary.Choice("Add a Stop", disabled="Please load or save a config file first" if not has_config else None),
                            questionary.Choice("Remove a Stop", disabled="Please load or save a config file first" if not has_config else None),
                            "Back"
                        ]
                    ).ask()
                    
                    if s_action == "Add a Stop":
                        asyncio.run(add_stop_wizard(config, config_path))
                    elif s_action == "Remove a Stop":
                        remove_stop_wizard(config, config_path)
                        
                elif c_action == "Change Number of Panels":
                    change_panels_wizard(config, config_path)

        elif action == "Simulator":
            rprint(f"[dim]Using config: {config_path}[/dim]")
            run_simulator(config, force_live=True)

        elif action == "Service Manager":

            if status == "UNSUPPORTED":
                print("Background service management is only supported on macOS.")
            else:
                manage_service_menu()

def hardware_monitor():
    known_ports = set(list_serial_ports())
    while True:
        time.sleep(1)
        current_ports = set(list_serial_ports())
        added = current_ports - known_ports
        removed = known_ports - current_ports
        
        if added or removed:
            # We print a carriage return and clear line to avoid messing up the questionary prompt too much
            sys.stdout.write("\r\033[K") 
            for p in added:
                rprint(f"[bold green]🔌 Hardware Device Connected:[/bold green] [cyan]{p}[/cyan] (Press Enter to refresh menu)")
            for p in removed:
                rprint(f"[bold yellow]🔌 Hardware Device Disconnected:[/bold yellow] [dim]{p}[/dim] (Press Enter to refresh menu)")
            known_ports = current_ports
            sys.stdout.flush()

def run_cli():
    # Start the hardware monitor as a daemon thread so it exits when the main thread exits
    monitor_thread = threading.Thread(target=hardware_monitor, daemon=True)
    monitor_thread.start()
    
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\nExiting...")
