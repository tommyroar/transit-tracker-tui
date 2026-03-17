import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

import rumps
import websockets.sync.client

from .cli import PLIST_NAME, get_service_status
from .config import TransitConfig, list_profiles, set_last_config_path, get_last_config_path
from .network.websocket_server import (
    SERVICE_STATE_FILE,
    get_service_state,
    get_last_service_update,
)
from .display import format_trip_line  # noqa: F401 — re-exported for backwards compat
from .transit_api import TransitAPI


class TransitTrackerApp(rumps.App):
    def __init__(self):
        super(TransitTrackerApp, self).__init__("Transit Tracker", title="🚉", quit_button=None)
        
        # 1. Initialize fixed menu items
        self.status_item = rumps.MenuItem("Status: Checking...")
        self.last_update_item = rumps.MenuItem("Last Proxy: Never")
        self.stats_item = rumps.MenuItem("Messages Processed: 0")
        
        # Profile Switcher
        self.profiles_menu = rumps.MenuItem("👥 Profiles")
        self.profiles_menu.add(rumps.MenuItem("Loading..."))
        
        # Rate Limit Alert
        self.rate_limit_item = rumps.MenuItem("✅ API Connection Healthy")
        
        # Create the sub-menu parent
        self.clients_menu = rumps.MenuItem("🛜 Clients (0)")
        self.clients_menu.add(rumps.MenuItem("No connections..."))
        
        self.restart_item = rumps.MenuItem("Restart Transit Tracker Proxy", callback=self.restart_service)
        self.shutdown_item = rumps.MenuItem("Shutdown Transit Tracker Proxy", callback=self.quit_app)
        
        # 2. Set the initial menu structure
        self.menu = [
            self.status_item,
            self.rate_limit_item,
            self.last_update_item,
            self.stats_item,
            rumps.separator,
            self.profiles_menu,
            self.clients_menu,
            rumps.separator,
            self.restart_item,
            self.shutdown_item
        ]
        
        self.api = TransitAPI()
        self.arrivals_cache = {} # stop_id -> list of arrivals
        self.display_trips = []  # ordered trip rows as shown on the LED display
        self.display_format = None  # loaded from config on first update
        self.profile_previews: Dict[str, List[dict]] = {}  # path -> trips (one-shot)
        self.cache_lock = threading.Lock()

        # Populate profiles menu immediately so it's not "Loading..." on first open
        try:
            profiles = list_profiles()
            active = get_last_config_path()
            if profiles:
                self.profiles_menu.clear()
                for p_path in profiles:
                    filename = os.path.basename(p_path)
                    is_active = p_path == active
                    prefix = "● " if is_active else "  "
                    item = rumps.MenuItem(f"{prefix}{filename}")
                    item.set_callback(self.switch_profile)
                    item.p_path = p_path
                    item.state = 1 if is_active else 0
                    try:
                        cfg = TransitConfig.load(p_path)
                        for sub in cfg.subscriptions:
                            item.add(rumps.MenuItem(f"{sub.label}: ..."))
                    except Exception:
                        item.add(rumps.MenuItem("Loading trips..."))
                    self.profiles_menu.add(item)
                self.last_profiles = profiles
        except Exception:
            pass

        self.timer = rumps.Timer(self.update_state, 2)
        self.timer.start()

        # Background thread for arrivals fetching (so we don't block the UI)
        self.bg_thread = threading.Thread(target=self.bg_fetch_loop, daemon=True)
        self.bg_thread.start()

        self.startup_time = time.time()
        self.last_client_ids = None
        self.is_rate_limited = False
        self.last_profiles = []
        self.last_update_ts = 0

    def _update_trips(self, trips):
        """Update arrivals cache and display trips from a trip list."""
        if not trips:
            return
        by_stop = {}
        for t in trips:
            stop = t.get("stopId", "")
            by_stop.setdefault(stop, []).append(t)
        with self.cache_lock:
            self.arrivals_cache.update(by_stop)
            self.display_trips = trips

    def _fetch_from_proxy(self):
        """Subscribe to the local proxy WebSocket and fetch one update."""
        config_path = get_last_config_path()
        if not config_path:
            return
        try:
            cfg = TransitConfig.load(config_path)
        except Exception:
            return

        self.display_format = cfg.transit_tracker.display_format
        pairs = []
        for sub in cfg.subscriptions:
            r_id = sub.route if ":" in sub.route else f"{sub.feed}:{sub.route}"
            s_id = sub.stop if ":" in sub.stop else f"{sub.feed}:{sub.stop}"
            off_sec = 0
            try:
                match = re.search(r"(-?\d+)", str(sub.time_offset))
                if match:
                    off_sec = int(match.group(1)) * 60
            except Exception:
                pass
            pairs.append(f"{r_id},{s_id},{off_sec}")

        sub_payload = {
            "event": "schedule:subscribe",
            "client_name": "BackgroundMonitor",
            "data": {"routeStopPairs": ";".join(pairs), "limit": 6}
        }

        try:
            with websockets.sync.client.connect("ws://localhost:8000", close_timeout=2) as ws:
                ws.send(json.dumps(sub_payload))
                msg = ws.recv(timeout=5)
                data = json.loads(msg)
                if data.get("event") == "schedule":
                    self._update_trips(data.get("data", {}).get("trips", []))
        except Exception:
            pass

    def _fetch_profile_preview(self, p_path: str):
        """Fetch a one-shot preview for an inactive profile via the public endpoint."""
        try:
            cfg = TransitConfig.load(p_path)
        except Exception:
            return
        if not cfg.subscriptions:
            return

        pairs = []
        for sub in cfg.subscriptions:
            r_id = sub.route if ":" in sub.route else f"{sub.feed}:{sub.route}"
            s_id = sub.stop if ":" in sub.stop else f"{sub.feed}:{sub.stop}"
            off_sec = 0
            try:
                match = re.search(r"(-?\d+)", str(sub.time_offset))
                if match:
                    off_sec = int(match.group(1)) * 60
            except Exception:
                pass
            pairs.append(f"{r_id},{s_id},{off_sec}")

        sub_payload = {
            "event": "schedule:subscribe",
            "client_name": "ProfilePreview",
            "data": {"routeStopPairs": ";".join(pairs), "limit": 6},
        }

        endpoint = cfg.transit_tracker.base_url
        try:
            with websockets.sync.client.connect(
                endpoint, close_timeout=3
            ) as ws:
                ws.send(json.dumps(sub_payload))
                msg = ws.recv(timeout=5)
                data = json.loads(msg)
                if data.get("event") == "schedule":
                    trips = data.get("data", {}).get("trips", [])
                    if trips:
                        with self.cache_lock:
                            self.profile_previews[p_path] = trips
        except Exception:
            pass

    def bg_fetch_loop(self):
        """Fetches trip data from the local proxy, then polls the state file.

        On startup, subscribes to the local WebSocket proxy for an immediate
        update, then fetches one-shot previews for inactive profiles.
        Falls back to reading last_message from service_state.json.
        """
        # Initial fetch from local proxy for immediate data
        self._fetch_from_proxy()

        # Fetch one-shot previews for inactive profiles
        try:
            active = get_last_config_path()
            for p_path in list_profiles():
                if p_path != active:
                    self._fetch_profile_preview(p_path)
        except Exception:
            pass

        while True:
            try:
                if os.path.exists(SERVICE_STATE_FILE):
                    with open(SERVICE_STATE_FILE, "r") as f:
                        state = json.load(f)
                    last_msg = state.get("last_message") or {}
                    self._update_trips(last_msg.get("data", {}).get("trips", []))
            except Exception:
                pass
            time.sleep(10)

    def update_state(self, _):
        try:
            is_running = False
            is_rate_limited = False
            client_count = 0
            last_update_str = "Never"
            client_details = []
            uptime_str = ""
            msg_count = 0
            state = {}
            current_config_path = get_last_config_path()
            
            # 1. Service Status Check
            if get_service_status():
                is_running = True
            
            if os.path.exists(SERVICE_STATE_FILE):
                try:
                    with open(SERVICE_STATE_FILE, "r") as f:
                        state = json.load(f)
                    
                    if not is_running:
                        pid = state.get("pid")
                        if pid:
                            try:
                                os.kill(pid, 0)
                                is_running = True
                            except OSError:
                                pass

                    if time.time() - state.get("heartbeat", 0) < 60:
                        client_count = state.get("client_count", 0)
                        client_details = state.get("clients", [])
                        msg_count = state.get("messages_processed", 0)
                        is_rate_limited = state.get("is_rate_limited", False)
                        
                        last_ts = state.get("last_update", 0)
                        if last_ts > 0:
                            self.last_update_ts = last_ts
                            last_update_str = datetime.fromtimestamp(last_ts).strftime('%H:%M:%S')
                        
                        start_ts = state.get("start_time", 0)
                        if start_ts > 0:
                            uptime_min = int(time.time() - start_ts) // 60
                            uptime_str = f" (up {uptime_min}m)" if uptime_min >= 1 else " (up <1m)"
                except Exception:
                    pass

            # 2. Auto-Quit if service is dead
            if not is_running:
                if time.time() - self.startup_time > 10:
                    rumps.quit_application()
                return

            # 3. Update titles of existing items
            self.status_item.title = f"Status: Running{uptime_str}"
            self.last_update_item.title = f"Last Proxy: {last_update_str}"
            self.stats_item.title = f"Messages Processed: {msg_count}"
            
            # 4. Update Profiles Menu
            profiles = list_profiles()
            # We rebuild the profiles menu if profiles list changed OR every 10 seconds to refresh arrivals
            # (Rumps is a bit limited for dynamic sub-items, so clearing/rebuilding is simplest)
            now = time.time()
            if profiles != self.last_profiles or int(now) % 10 == 0:
                self.profiles_menu.clear()
                if not profiles:
                    self.profiles_menu.add(rumps.MenuItem("No profiles found"))
                else:
                    for p_path in profiles:
                        filename = os.path.basename(p_path)
                        is_active = p_path == current_config_path
                        prefix = "● " if is_active else "  "
                        
                        # Parent Profile Item
                        profile_root = rumps.MenuItem(f"{prefix}{filename}")
                        
                        # Add a callback to switch to this profile
                        profile_root.set_callback(self.switch_profile)
                        profile_root.p_path = p_path
                        profile_root.state = 1 if is_active else 0
                        
                        # Add text simulator rows from live or preview trips
                        try:
                            with self.cache_lock:
                                if is_active:
                                    trips = list(self.display_trips)
                                else:
                                    trips = list(self.profile_previews.get(p_path, []))
                            if trips:
                                fmt = self.display_format if is_active else None
                                for t in trips:
                                    profile_root.add(rumps.MenuItem(format_trip_line(t, now, fmt=fmt)))
                            else:
                                cfg = TransitConfig.load(p_path)
                                for sub in cfg.subscriptions:
                                    profile_root.add(rumps.MenuItem(f"{sub.label}: ..."))
                        except Exception:
                            profile_root.add(rumps.MenuItem("Error loading stops"))
                        
                        # Add metadata info
                        profile_root.add(rumps.MenuItem("─────────────"))  # visual divider, no callback
                        profile_root.add(rumps.MenuItem(f"File: {p_path}"))
                        if is_active:
                            refresh_str = datetime.fromtimestamp(self.last_update_ts).strftime('%H:%M:%S') if self.last_update_ts else "Never"
                            profile_root.add(rumps.MenuItem(f"Last Refresh: {refresh_str}"))
                        
                        self.profiles_menu.add(profile_root)
                self.last_profiles = profiles

            # 5. Update the Clients Sub-menu and its Title
            self.clients_menu.title = f"🛜 Clients ({client_count})"

            # Update Rate Limit Status — always sync from service state
            self.is_rate_limited = is_rate_limited
            throttle_total = state.get("throttle_total", 0) if os.path.exists(SERVICE_STATE_FILE) else 0
            api_calls = state.get("api_calls_total", 0) if os.path.exists(SERVICE_STATE_FILE) else 0
            throttle_rate = state.get("throttle_rate", 0) if os.path.exists(SERVICE_STATE_FILE) else 0

            if is_rate_limited:
                self.title = "📵"
                self.rate_limit_item.title = f"📵 Rate Limited — {throttle_total}/{api_calls} throttled ({throttle_rate:.0%})"
            else:
                self.title = "🚉"
                self.rate_limit_item.title = f"✅ Healthy — {throttle_total}/{api_calls} throttled ({throttle_rate:.0%})" if api_calls > 0 else "✅ API Connection Healthy"
            
            current_client_ids = ",".join(sorted([c.get("address", "") for c in client_details]))
            if current_client_ids != self.last_client_ids:
                self.clients_menu.clear()
                if client_count > 0:
                    for c in client_details:
                        name = c.get("name", "Unknown")
                        addr = c.get("address", "0.0.0.0").split(":")[0]
                        self.clients_menu.add(rumps.MenuItem(f"{name} ({addr})"))
                else:
                    self.clients_menu.add(rumps.MenuItem("No connections"))
                
                self.last_client_ids = current_client_ids

        except Exception:
            pass

    def switch_profile(self, sender):
        p_path = getattr(sender, "p_path", None)
        if not p_path: return
        print(f"[GUI] Switching to profile: {p_path}")
        set_last_config_path(p_path)
        try:
            cfg = TransitConfig.load(p_path)
            self.display_format = cfg.transit_tracker.display_format
        except Exception:
            pass
        rumps.notification("Transit Tracker", "Profile Switched", f"Active: {os.path.basename(p_path)}")

    def restart_service(self, _):
        """Restarts the background service via launchctl."""
        label = PLIST_NAME.replace(".plist", "")
        plist_path = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_NAME}")
        
        rumps.notification("Transit Tracker", "Service Restart", "Restarting background proxy...")
        
        if os.path.exists(plist_path):
            subprocess.run(["launchctl", "unload", plist_path], capture_output=True)
            time.sleep(1)
            subprocess.run(["launchctl", "load", plist_path], capture_output=True)
        else:
            # Fallback for manual restart if plist is missing
            subprocess.run(["pkill", "-f", "transit-tracker service"], capture_output=True)
            time.sleep(1)
            subprocess.Popen([sys.executable, "-m", "transit_tracker.cli", "service"])

    def quit_app(self, _):
        label = PLIST_NAME.replace(".plist", "")
        plist_path = os.path.expanduser(f"~/Library/LaunchAgents/{PLIST_NAME}")
        if os.path.exists(plist_path):
            subprocess.run(["launchctl", "unload", plist_path], capture_output=True)
        
        if os.path.exists(SERVICE_STATE_FILE):
            try:
                with open(SERVICE_STATE_FILE, "r") as f:
                    state = json.load(f)
                pid = state.get("pid")
                if pid:
                    os.kill(pid, 15)
            except Exception:
                pass
        rumps.quit_application()

def main():
    print("[GUI] Starting singleton check...")
    import tempfile
    pid_file = os.path.join(tempfile.gettempdir(), "transit_tracker_gui.pid")
    
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            print(f"[GUI] Found existing PID file with PID {old_pid}")
            os.kill(old_pid, 0)
            print("[GUI] Existing process is alive. Exiting.")
            return 
        except (OSError, ValueError, ProcessLookupError):
            print("[GUI] Existing process is dead or invalid PID. Continuing.")
            pass 
            
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))
    print(f"[GUI] Created PID file at {pid_file} with PID {os.getpid()}")
        
    try:
        print("[GUI] Launching TransitTrackerApp...")
        app = TransitTrackerApp()
        app.run()
    except Exception as e:
        print(f"[GUI] Error launching app: {e}")
    finally:
        print("[GUI] Cleaning up PID file...")
        if os.path.exists(pid_file):
            os.remove(pid_file)

if __name__ == "__main__":
    main()
