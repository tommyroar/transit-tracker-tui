import os
import json
import rumps
import subprocess
import time
import sys
from datetime import datetime
from .network.websocket_server import SERVICE_STATE_FILE
from .cli import PLIST_NAME

# Setup logging for the GUI
LOG_FILE = os.path.join(os.path.expanduser("~/.config/transit-tracker"), "gui.log")

def log_error(msg):
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{datetime.now()} - {msg}\n")
    except Exception:
        pass

class TransitTrackerApp(rumps.App):
    def __init__(self):
        super(TransitTrackerApp, self).__init__("Transit Tracker", title="🚉", quit_button=None)
        
        self.status_item = rumps.MenuItem("Status: Checking...")
        self.last_update_item = rumps.MenuItem("🔄 Last Proxy: Never")
        self.stats_item = rumps.MenuItem("📊 Messages: 0")
        self.shutdown_item = rumps.MenuItem("Shutdown Transit Tracker Proxy", callback=self.quit_app)
        
        # Initial menu structure
        self.menu = [
            self.status_item,
            self.last_update_item,
            self.stats_item,
            rumps.separator,
            "🛜 No clients connected",
            rumps.separator,
            self.shutdown_item
        ]
        
        self.timer = rumps.Timer(self.update_state, 2)
        self.timer.start()
        self.startup_time = time.time()

    def update_state(self, _):
        try:
            is_running = False
            client_count = 0
            last_update_str = "Never"
            client_details = []
            uptime_str = ""
            msg_count = 0
            
            # 1. Check Service Status
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

                    # Freshness check: 60 seconds
                    if time.time() - state.get("heartbeat", 0) < 60:
                        client_count = state.get("client_count", 0)
                        client_details = state.get("clients", [])
                        msg_count = state.get("messages_processed", 0)
                        
                        last_ts = state.get("last_update", 0)
                        if last_ts > 0:
                            last_update_str = datetime.fromtimestamp(last_ts).strftime('%H:%M:%S')
                        
                        start_ts = state.get("start_time", 0)
                        if start_ts > 0:
                            uptime_sec = int(time.time() - start_ts)
                            uptime_min = uptime_sec // 60
                            if uptime_min < 1:
                                uptime_str = f" (up <1m)"
                            else:
                                uptime_str = f" (up {uptime_min}m)"
                except Exception as e:
                    log_error(f"Error reading state file: {e}")

            # 2. Handle Auto-Quit if service is dead
            if not is_running:
                if time.time() - self.startup_time > 10:
                    rumps.quit_application()
                return

            # 3. Rebuild Menu with Live Data
            self.status_item.title = f"🟢 Status: Running{uptime_str}"
            self.last_update_item.title = f"🔄 Last Proxy: {last_update_str}"
            self.stats_item.title = f"📊 Messages: {msg_count}"
            
            new_menu = [
                self.status_item,
                self.last_update_item,
                self.stats_item,
                rumps.separator
            ]
            
            if client_count > 0:
                new_menu.append(f"🛜 Clients Connected ({client_count}):")
                for c in client_details:
                    name = c.get("name", "Unknown")
                    addr_full = c.get("address", "0.0.0.0")
                    addr = addr_full.split(":")[0] if ":" in addr_full else addr_full
                    new_menu.append(rumps.MenuItem(f"  • {name} ({addr})"))
            else:
                new_menu.append("🛜 Waiting for connections...")
                
            new_menu.append(rumps.separator)
            new_menu.append(self.shutdown_item)
            
            self.menu.clear()
            self.menu.update(new_menu)

        except Exception as e:
            log_error(f"Global error in update_state: {e}")

    def quit_app(self, _):
        """Kills the proxy service and quits the tray app."""
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
    app = TransitTrackerApp()
    app.run()

if __name__ == "__main__":
    main()
