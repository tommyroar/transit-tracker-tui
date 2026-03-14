import json
import os
import subprocess
import time
from datetime import datetime

import rumps

from .cli import PLIST_NAME
from .network.websocket_server import SERVICE_STATE_FILE


class TransitTrackerApp(rumps.App):
    def __init__(self):
        super(TransitTrackerApp, self).__init__("Transit Tracker", title="🚉", quit_button=None)
        
        # 1. Initialize fixed menu items
        self.status_item = rumps.MenuItem("Status: Checking...")
        self.last_update_item = rumps.MenuItem("Last Proxy: Never")
        self.stats_item = rumps.MenuItem("Messages Processed: 0")
        
        # Rate Limit Alert
        self.rate_limit_item = rumps.MenuItem("✅ API Connection Healthy")
        
        # Create the sub-menu parent
        self.clients_menu = rumps.MenuItem("🛜 Clients (0)")
        
        self.shutdown_item = rumps.MenuItem("Shutdown Transit Tracker Proxy", callback=self.quit_app)
        
        # 2. Set the initial menu structure
        self.menu = [
            self.status_item,
            self.rate_limit_item,
            self.last_update_item,
            self.stats_item,
            rumps.separator,
            self.clients_menu,
            rumps.separator,
            self.shutdown_item
        ]
        
        self.timer = rumps.Timer(self.update_state, 2)
        self.timer.start()
        self.startup_time = time.time()
        self.last_client_ids = None
        self.is_rate_limited = False

    def update_state(self, _):
        try:
            is_running = False
            is_rate_limited = False
            client_count = 0
            last_update_str = "Never"
            client_details = []
            uptime_str = ""
            msg_count = 0
            
            # 1. Service Status Check
            label = PLIST_NAME.replace(".plist", "")
            res = subprocess.run(["launchctl", "list", label], capture_output=True, text=True)
            if res.returncode == 0:
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
            
            # 4. Update the Clients Sub-menu and its Title
            self.clients_menu.title = f"🛜 Clients ({client_count})"
            
            # Update Rate Limit Status
            if is_rate_limited != self.is_rate_limited:
                self.is_rate_limited = is_rate_limited
                self.title = "⚠️" if is_rate_limited else "🚉"
                self.rate_limit_item.set_callback(None) # Make it look like a label
                # In rumps, visibility is tricky, we'll just change the title or add/remove
                if is_rate_limited:
                    self.rate_limit_item.title = "⚠️ OneBusAway Rate Limited!"
                else:
                    self.rate_limit_item.title = "✅ API Connection Healthy"
            
            current_client_ids = ",".join(sorted([c.get("address", "") for c in client_details]))
            if current_client_ids != self.last_client_ids:
                self.clients_menu.clear()
                if client_count > 0:
                    for c in client_details:
                        name = c.get("name", "Unknown")
                        addr = c.get("address", "0.0.0.0").split(":")[0]
                        self.clients_menu.add(rumps.MenuItem(f"{name} ({addr})"))
                else:
                    self.clients_menu.add(rumps.MenuItem("Waiting for connections..."))
                
                self.last_client_ids = current_client_ids

        except Exception:
            pass

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
    # Singleton check to prevent multiple tray icons
    import tempfile
    pid_file = os.path.join(tempfile.gettempdir(), "transit_tracker_gui.pid")
    
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                old_pid = int(f.read().strip())
            print(f"[GUI] Found existing PID file with PID {old_pid}")
            # Check if process is actually running
            os.kill(old_pid, 0)
            print("[GUI] Existing process is alive. Exiting.")
            return # Already running
        except (OSError, ValueError, ProcessLookupError):
            print("[GUI] Existing process is dead or invalid PID. Continuing.")
            pass # Stale pid file
            
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
