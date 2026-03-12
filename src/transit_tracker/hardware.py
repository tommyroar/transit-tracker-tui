import json
import time
from typing import Optional, List, Dict, Any
import serial
import serial.tools.list_ports
from rich.console import Console

console = Console()

class EntityType:
    TEXT = 1
    SELECT = 2
    SWITCH = 3
    BUTTON = 4

class ESPHomeFlasher:
    def __init__(self, port_name: str, baudrate: int = 115200):
        self.port_name = port_name
        self.baudrate = baudrate
        self.serial = None
        self.request_id = 1

    def __enter__(self):
        self.serial = serial.Serial(self.port_name, self.baudrate, timeout=2)
        # Clear any initial garbage
        self.serial.reset_input_buffer()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.serial and self.serial.is_open:
            self.serial.close()

    def send_request(self, method: str, params: Dict[str, Any]) -> Any:
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": self.request_id
        }
        self.request_id += 1
        
        payload = "JRPC:" + json.dumps(req) + "\r\n"
        self.serial.write(payload.encode("utf-8"))
        
        # Read the response (naive blocking read for the id)
        start_time = time.time()
        while time.time() - start_time < 5.0: # 5 second timeout
            line = self.serial.readline()
            if line and line.startswith(b"JRPC:"):
                try:
                    resp = json.loads(line[5:].decode("utf-8").strip())
                    if resp.get("id") == req["id"]:
                        if "result" in resp:
                            return resp["result"]
                        return None
                except json.JSONDecodeError:
                    pass
        return None

    def set_entity(self, entity_id: str, entity_type: int, value: Any) -> bool:
        res = self.send_request("entity.set", {
            "id": entity_id,
            "type": entity_type,
            "value": value
        })
        return res.get("success", False) if res else False

    def get_entity(self, entity_id: str, entity_type: int) -> Optional[Any]:
        return self.send_request("entity.get", {
            "id": entity_id,
            "type": entity_type
        })

    def press_button(self, entity_id: str) -> bool:
        res = self.send_request("entity.set", {
            "id": entity_id,
            "type": EntityType.BUTTON
        })
        return res.get("success", False) if res else False

def get_usb_devices() -> List[Dict[str, str]]:
    """Returns a list of USB devices with port, name, and model information."""
    ports = serial.tools.list_ports.comports()
    usb_devices = []
    
    # Mapping of common Vendor/Product IDs to human-readable names
    model_map = {
        (0x303a, 0x1001): "ESP32-S3 (Built-in USB)",
        (0x303a, 0x80c2): "ESP32-S3 (Generic)",
        (0x1a86, 0x7523): "ESP32/WCH CH340",
        (0x10c4, 0xea60): "ESP32/CP210x",
        (0x0403, 0x6001): "ESP32/FTDI",
    }
    
    for port in ports:
        is_usb = False
        if port.hwid != "n/a" and "USB" in port.hwid:
            is_usb = True
        elif "usb" in port.device.lower():
            is_usb = True
            
        if is_usb:
            model = "Unknown Device"
            if port.vid and port.pid:
                model = model_map.get((port.vid, port.pid), f"USB Device ({hex(port.vid)}:{hex(port.pid)})")
            
            if model == "Unknown Device" and port.manufacturer and "Espressif" in port.manufacturer:
                model = "Espressif Controller"
                
            name = port.description if port.description != "n/a" else port.device
            
            usb_devices.append({
                "port": port.device,
                "name": name,
                "model": model,
                "manufacturer": port.manufacturer or "Unknown"
            })
            
    return usb_devices

def list_serial_ports() -> List[str]:
    """Simple list of valid serial port paths."""
    return [d["port"] for d in get_usb_devices()]

def load_hardware_config(port: str, config) -> bool:
    """
    Attempts to read current configuration from the ESP32 and merge it into the provided config object.
    Returns True if successful.
    """
    from .config import TransitSubscription
    
    with console.status(f"[bold cyan]Reading configuration from {port}...") as status:
        try:
            with ESPHomeFlasher(port) as flasher:
                status.update("[cyan]Reading Base URL...")
                base_url = flasher.get_entity("base_url_config", EntityType.TEXT)
                if base_url and "value" in base_url:
                    # Strip wss:// etc if needed, but we keep full url in our config
                    config.api_url = base_url["value"]

                status.update("[cyan]Reading Schedule...")
                schedule = flasher.get_entity("schedule_config", EntityType.TEXT)
                if schedule and "value" in schedule:
                    sched_str = schedule["value"]
                    if sched_str:
                        # Schedule format on device: routeId,stopId,offset;routeId,stopId,offset
                        parts = sched_str.split(";")
                        new_subs = []
                        for part in parts:
                            if not part: continue
                            chunks = part.split(",")
                            if len(chunks) >= 2:
                                r_id = chunks[0]
                                s_id = chunks[1]
                                
                                # Device might store offsets in seconds (e.g. -420 for -7min)
                                offset_str = None
                                if len(chunks) >= 3:
                                    try:
                                        offset_sec = int(chunks[2])
                                        if offset_sec != 0:
                                            # Convert seconds back to "-Xmin" format
                                            offset_min = offset_sec // 60
                                            offset_str = f"{offset_min}min"
                                    except ValueError:
                                        pass
                                
                                # Device might use prefixes like st:1_100039 or just 1_100039
                                route_clean = r_id.split(":")[-1] if ":" in r_id else r_id
                                
                                # Try to guess feed
                                agency_id = route_clean.split("_")[0] if "_" in route_clean else ""
                                feed = "st" if agency_id == "40" else "kcm" if agency_id == "1" else "st"
                                
                                new_subs.append(TransitSubscription(
                                    feed=feed,
                                    route=r_id,
                                    stop=s_id,
                                    label=f"Hardware Stop ({r_id})", # We lose label name on HW
                                    time_offset=offset_str
                                ))
                        # Only replace if we actually found stops
                        if new_subs:
                            config.subscriptions = new_subs
            
            console.print("[bold green]Successfully read configuration from device![/bold green]")
            return True
        except Exception as e:
            console.print(f"[bold red]Failed to read device:[/bold red] {e}")
            return False

def flash_hardware(port: str, config) -> bool:
    with console.status(f"[bold cyan]Flashing hardware on {port}...") as status:
        try:
            with ESPHomeFlasher(port) as flasher:
                status.update("[cyan]Configuring Base URL...")
                flasher.set_entity("base_url_config", EntityType.TEXT, config.api_url)
                
                status.update("[cyan]Configuring Schedule...")
                # Map subscriptions to schedule_config (routeId,stopId,offset)
                schedule_parts = []
                for sub in config.subscriptions:
                    # Parse time_offset string like "-7min" into seconds
                    offset_sec = 0
                    if sub.time_offset:
                        try:
                            # Strip "min" and whitespace, then multiply by 60
                            clean_str = sub.time_offset.lower().replace("min", "").strip()
                            offset_sec = int(clean_str) * 60
                        except ValueError:
                            pass
                    schedule_parts.append(f"{sub.route},{sub.stop},{offset_sec}")
                schedule_str = ";".join(schedule_parts)
                flasher.set_entity("schedule_config", EntityType.TEXT, schedule_str)
                
                status.update("[cyan]Configuring Display Settings...")
                flasher.set_entity("time_display_config", EntityType.SELECT, "arrival")
                flasher.set_entity("list_mode_config", EntityType.SELECT, "sequential")
                flasher.set_entity("time_units_config", EntityType.SELECT, "short")
                flasher.set_entity("scroll_headsigns", EntityType.SWITCH, "ON")

                status.update("[cyan]Saving Preferences...")
                flasher.press_button("write_preferences")
                time.sleep(1) # Give it a moment to write to flash
                
                status.update("[cyan]Reloading Tracker...")
                flasher.press_button("reload_tracker")
            
            console.print("[bold green]Successfully flashed hardware device![/bold green]")
            return True
        except Exception as e:
            console.print(f"[bold red]Failed to flash device:[/bold red] {e}")
            return False
