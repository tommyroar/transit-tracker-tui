import os
import json
import rumps
import subprocess
import time
from threading import Thread
from .network.websocket_server import SERVICE_STATE_FILE

class TransitTrackerApp(rumps.App):
    def __init__(self):
        super(TransitTrackerApp, self).__init__("🚉", title=None, quit_button="Quit Transit Tracker")
        self.menu = [
            rumps.MenuItem("Status: Checking...", callback=None),
            rumps.MenuItem("Clients: 0", callback=None),
            None, # Separator
            "Open Dashboard (TUI)",
            "Start Service",
            "Stop Service",
            None, # Separator
        ]
        self.status_item = self.menu["Status: Checking..."]
        self.clients_item = self.menu["Clients: 0"]
        
        # Start a background thread to watch the state file
        self.watcher_thread = Thread(target=self.update_state_loop, daemon=True)
        self.watcher_thread.start()

    def update_state_loop(self):
        while True:
            try:
                if os.path.exists(SERVICE_STATE_FILE):
                    with open(SERVICE_STATE_FILE, "r") as f:
                        state = json.load(f)
                    
                    # Check if PID is still alive
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
                        self.title = "🚉"
                    else:
                        self.status_item.title = "🔴 Status: Stopped"
                        self.clients_item.title = "📱 Clients: 0"
                        self.title = "🚉" 
                else:
                    self.status_item.title = "⚪ Status: Not Started"
                    self.title = "🚉"
            except Exception:
                pass
            time.sleep(2)

    @rumps.clicked("Open Dashboard (TUI)")
    def open_dashboard(self, _):
        # Open a new terminal window running the transit-tracker ui
        cmd = "osascript -e 'tell application \"Terminal\" to do script \"transit-tracker ui\"'"
        subprocess.run(cmd, shell=True)

    @rumps.clicked("Start Service")
    def start_service(self, _):
        # Start the service in the background
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
