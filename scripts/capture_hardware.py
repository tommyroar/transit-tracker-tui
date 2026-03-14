import json
import time
from typing import Any, Dict, Optional

from transit_tracker.hardware import EntityType, ESPHomeFlasher, list_serial_ports


class CapturingFlasher(ESPHomeFlasher):
    def __init__(self, port_name: str, baudrate: int = 115200):
        super().__init__(port_name, baudrate)
        self.captured_traffic = []

    def send_request(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self.request_id
        }
        if params is not None:
            req["params"] = params
        self.request_id += 1
        
        payload = "JRPC:" + json.dumps(req) + "\r\n"
        self.serial.write(payload.encode("utf-8"))
        
        # Log request
        self.captured_traffic.append({
            "type": "tx",
            "payload": req,
            "timestamp": time.time()
        })
        
        start_time = time.time()
        while time.time() - start_time < 5.0:
            line = self.serial.readline()
            if line and line.startswith(b"JRPC:"):
                try:
                    resp = json.loads(line[5:].decode("utf-8").strip())
                    
                    self.captured_traffic.append({
                        "type": "rx",
                        "payload": resp,
                        "timestamp": time.time()
                    })
                    
                    if resp.get("id") == req["id"]:
                        if "result" in resp:
                            return resp["result"]
                        return None
                except json.JSONDecodeError:
                    pass
        
        # Log timeout
        self.captured_traffic.append({
            "type": "timeout",
            "request_id": req["id"],
            "timestamp": time.time()
        })
        return None

def main():
    print("Waiting for Transit Tracker device to be plugged in...")
    port = None
    while True:
        ports = list_serial_ports()
        if ports:
            port = ports[0]
            print(f"Found device on {port}!")
            # give it a moment to settle
            time.sleep(2)
            break
        time.sleep(1)

    print(f"Connecting to {port} and downloading current configuration...")
    
    with CapturingFlasher(port) as flasher:
        # Get Device Info
        info = flasher.get_device_info()
        print(f"Device Info: {info}")
        
        # We need to know what entities are available to download everything,
        # but the ESPHome RPC doesn't have an 'entity.list' method that works simply in our implementation.
        # We can poll the known ones.
        known_text_entities = [
            "base_url_config",
            "schedule_config",
            "abbreviations_config",
            "route_styles_config",
            "now_str_config",
            "min_long_str_config",
            "min_short_str_config",
            "hours_short_str_config",
            "feed_code_config"
        ]
        
        known_select_entities = [
            "time_display_config",
            "time_units_config",
            "list_mode_config"
        ]
        
        known_switch_entities = [
            "scroll_headsigns",
            "flip_display"
        ]
        
        config_state = {}
        
        for entity_id in known_text_entities:
            print(f"Fetching {entity_id}...")
            val = flasher.get_entity(entity_id, EntityType.TEXT)
            config_state[entity_id] = val
            
        for entity_id in known_select_entities:
            print(f"Fetching {entity_id}...")
            val = flasher.get_entity(entity_id, EntityType.SELECT)
            config_state[entity_id] = val
            
        for entity_id in known_switch_entities:
            print(f"Fetching {entity_id}...")
            val = flasher.get_entity(entity_id, EntityType.SWITCH)
            config_state[entity_id] = val
            
        capture_data = {
            "device_info": info,
            "parsed_config": config_state,
            "raw_traffic": flasher.captured_traffic
        }
        
        with open("hardware_capture.json", "w") as f:
            json.dump(capture_data, f, indent=2)
            
        print("Data captured successfully to hardware_capture.json!")
        print("You can now unplug the device and resume normal operation.")

if __name__ == "__main__":
    main()
