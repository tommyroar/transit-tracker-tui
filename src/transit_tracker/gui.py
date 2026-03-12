import os
import json
import rumps
import subprocess
import time
from threading import Thread
from .network.websocket_server import SERVICE_STATE_FILE
from .cli import TUI_STATE_FILE

class TransitTrackerApp(rumps.App):
    def __init__(self):
        super(TransitTrackerApp, self).__init__("🚉", title=None, quit_button=None)
        self.menu = [
            rumps.MenuItem("Status: Checking...", callback=None),
            rumps.MenuItem("Clients: 0", callback=None),
            None, # Separator
            "Open Dashboard (TUI)",
            "Start Service",
            "Stop Service",
            None, # Separator
            rumps.MenuItem("Quit Transit Tracker", callback=self.quit_app)
        ]
        self.status_item = self.menu["Status: Checking..."]
        self.clients_item = self.menu["Clients: 0"]
        
        # Start a background thread to watch the state file
        self.watcher_thread = Thread(target=self.update_state_loop, daemon=True)
        self.watcher_thread.start()

    def update_state_loop(self):
        # We wait a few seconds before starting auto-quit check 
        # to allow the TUI that launched us to register its PID
        time.sleep(5)
        
        while True:
            try:
                # 1. Update Service Status (Background Proxy)
                if os.path.exists(SERVICE_STATE_FILE):
                    with open(SERVICE_STATE_FILE, "r") as f:
                        state = json.load(f)
                    pid = state.get("pid")
                    is_running = False
                    if pid:
                        try:
                            os.kill(pid, 0)
                            is_running = True
                        except OSError:
                            pass
                    
                    if is_running:
                        self.status_item.title = "🟢 Status: Running"
                        self.clients_item.title = f"📱 Clients: {state.get('client_count', 0)}"
                    else:
                        self.status_item.title = "🔴 Status: Stopped"
                        self.clients_item.title = "📱 Clients: 0"
                else:
                    self.status_item.title = "⚪ Status: Not Started"

                # 2. Check TUI sessions and auto-quit if NONE are left
                if os.path.exists(TUI_STATE_FILE):
                    with open(TUI_STATE_FILE, "r") as f:
                        tui_state = json.load(f)
                    
                    pids = tui_state.get("pids", [])
                    active_pids = []
                    for p in pids:
                        try:
                            os.kill(p, 0) # Check if alive
                            active_pids.append(p)
                        except (OSError, ProcessLookupError):
                            continue
                    
                    # If we cleaned up some dead PIDs, sync back to file
                    if len(active_pids) != len(pids):
                        with open(TUI_STATE_FILE, "w") as f:
                            json.dump({"pids": active_pids}, f)
                    
                    # AUTO-QUIT if no TUI sessions are active
                    if not active_pids:
                        rumps.quit_application()
                else:
                    # No state file means no TUI registered yet (or ever)
                    # We quit since the tray is an accompaniment to the TUI
                    rumps.quit_application()

            except Exception:
                pass
            time.sleep(2)

    def quit_app(self, _):
        # Kill ALL TUI sessions if user manually quits the tray
        if os.path.exists(TUI_STATE_FILE):
            try:
                with open(TUI_STATE_FILE, "r") as f:
                    state = json.load(f)
                for pid in state.get("pids", []):
                    try:
                        os.kill(pid, 2) # SIGINT
                        time.sleep(0.2)
                        os.kill(pid, 9) # SIGKILL
                    except (OSError, ProcessLookupError):
                        pass
                os.remove(TUI_STATE_FILE)
            except Exception:
                pass
        rumps.quit_application()

    @rumps.clicked("Open Dashboard (TUI)")
    def open_dashboard(self, _):
        cmd = "osascript -e 'tell application \"Terminal\" to do script \"transit-tracker ui\"'"
        subprocess.run(cmd, shell=True)

    @rumps.clicked("Start Service")
    def start_service(self, _):
        subprocess.Popen(["transit-tracker", "service"], start_new_session=True)
        rumps.notification("Transit Tracker", "Service Starting", "The background proxy service is starting up.")

    @rumps.clicked("Stop Service")
    def stop_service(self, _):
        if os.path.exists(SERVICE_STATE_FILE):
            try:
                with open(SERVICE_STATE_FILE, "r") as f:
                    state = json.load(f)
                pid = state.get("pid")
                if pid:
                    os.kill(pid, 15) # SIGTERM
                    rumps.notification("Transit Tracker", "Service Stopped", "The background proxy service has been stopped.")
            except Exception as e:
                rumps.alert(f"Error stopping service: {e}")

def main():
    app = TransitTrackerApp()
    app.run()

if __name__ == "__main__":
    main()
