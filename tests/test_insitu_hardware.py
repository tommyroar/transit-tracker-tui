import json
from unittest.mock import patch

import pytest

from transit_tracker.config import TransitConfig
from transit_tracker.hardware import load_hardware_config

pytestmark = pytest.mark.integration

# Load the captured JSON to use as our source of truth
with open("hardware_capture.json", "r") as f:
    capture_data = json.load(f)

def test_load_hardware_config_against_capture():
    """
    Validates that our hardware read logic correctly parses real data
    captured from an in-situ Transit Tracker device.
    """
    
    # We will mock serial.Serial and replay the captured rx data when we receive the tx data
    class MockSerialInSitu:
        def __init__(self, *args, **kwargs):
            self.is_open = True
            self.traffic = capture_data["raw_traffic"]
            self.tx_index = 0
            self.rx_buffer = []
            
        def reset_input_buffer(self):
            pass
            
        def write(self, data):
            # Parse what we sent
            written_str = data.decode("utf-8")
            if written_str.startswith("JRPC:"):
                sent_json = json.loads(written_str[5:-2])
                req_id = sent_json.get("id")
                method = sent_json.get("method")
                params = sent_json.get("params", {})
                
                # Find the matching rx in traffic by looking for the tx that caused it
                for i, packet in enumerate(self.traffic):
                    if packet["type"] == "tx" and packet["payload"].get("method") == method:
                        if packet["payload"].get("params", {}) == params:
                            # The next rx packet with the same id in the capture is the response
                            cap_id = packet["payload"].get("id")
                            for rx_packet in self.traffic[i:]:
                                if rx_packet["type"] == "rx" and rx_packet["payload"].get("id") == cap_id:
                                    # We found the response! Rewrite the ID to match our current request
                                    resp_payload = dict(rx_packet["payload"])
                                    resp_payload["id"] = req_id
                                    self.rx_buffer.append(f"JRPC:{json.dumps(resp_payload)}\r\n".encode("utf-8"))
                                    break
                            break
            return len(data)
            
        def readline(self):
            if self.rx_buffer:
                return self.rx_buffer.pop(0)
            return b""
            
        def close(self):
            self.is_open = False

    config = TransitConfig()
    
    with patch("transit_tracker.hardware.serial.Serial", MockSerialInSitu):
        # Time patch so timeouts don't hang if there's a missing packet
        time_values = [float(i) * 0.1 for i in range(10000)]
        with patch("transit_tracker.hardware.time.time", side_effect=lambda: time_values.pop(0)):
            success = load_hardware_config("/dev/tty.mock", config)            
    assert success is True, "Failed to load hardware config from capture"
    
    # Assert base_url was extracted correctly
    assert config.api_url == "wss://tt.horner.tj/"
    
    # The captured schedule is: "st:40_100240,st:1_8494,-420;st:1_100039,st:1_11920,-540"
    # That means two subscriptions:
    # 1. route: st:40_100240, stop: st:1_8494, offset: -420s -> -7min
    # 2. route: st:1_100039, stop: st:1_11920, offset: -540s -> -9min
    
    assert len(config.subscriptions) == 2
    
    sub1 = config.subscriptions[0]
    assert sub1.route == "st:40_100240"
    assert sub1.stop == "st:1_8494"
    assert sub1.time_offset == "-7min"
    
    sub2 = config.subscriptions[1]
    assert sub2.route == "st:1_100039"
    assert sub2.stop == "st:1_11920"
    assert sub2.time_offset == "-9min"

if __name__ == "__main__":
    pytest.main([__file__])

def test_flash_hardware_serialization():
    """
    Ensures that flash_hardware can successfully serialize our config back to 
    the format expected by the hardware, utilizing the same Mock pattern.
    """
    class MockSerialTx:
        writes = []
        def __init__(self, *args, **kwargs):
            self.is_open = True
            self.rx_buffer = []
            
        def reset_input_buffer(self):
            pass
            
        def write(self, data):
            written_str = data.decode("utf-8")
            if written_str.startswith("JRPC:"):
                sent_json = json.loads(written_str[5:-2])
                self.__class__.writes.append(sent_json)
                req_id = sent_json.get("id")
                # Immediately queue a success response
                resp = {"jsonrpc": "2.0", "id": req_id, "result": {"success": True}}
                self.rx_buffer.append(f"JRPC:{json.dumps(resp)}\r\n".encode("utf-8"))
            return len(data)
            
        def readline(self):
            if self.rx_buffer:
                return self.rx_buffer.pop(0)
            return b""
            
        def close(self):
            self.is_open = False

    MockSerialTx.writes = []

    config = TransitConfig()
    config.service.use_local_api = False
    config.transit_tracker.base_url = "wss://tt.horner.tj/"
    config.api_url = "wss://tt.horner.tj/"
    
    # Fake some subscriptions similar to capture
    from transit_tracker.config import TransitSubscription
    config.subscriptions = [
        TransitSubscription(feed="st", route="st:40_100240", stop="st:1_8494", label="", time_offset="-7min"),
        TransitSubscription(feed="st", route="st:1_100039", stop="st:1_11920", label="", time_offset="-9min")
    ]
    
    with patch("transit_tracker.hardware.serial.Serial", MockSerialTx):
        with patch("transit_tracker.hardware.time.sleep"):
            from transit_tracker.hardware import flash_hardware
            # Provide time patches so it doesn't timeout
            time_values = [float(i) * 0.1 for i in range(10000)]
            with patch("transit_tracker.hardware.time.time", side_effect=lambda: time_values.pop(0)):
                success = flash_hardware("/dev/tty.mock", config)
                
    assert success is True
    
    # Extract writes
    writes = MockSerialTx.writes
    
    # Find schedule_config write
    sched_write = next((w for w in writes if w.get("method") == "entity.set" and w.get("params", {}).get("id") == "schedule_config"), None)
    assert sched_write is not None
    assert sched_write["params"]["value"] == "st:40_100240,st:1_8494,-420;st:1_100039,st:1_11920,-540"

