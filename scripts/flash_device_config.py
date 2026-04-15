#!/usr/bin/env python3
"""Push base_url + schedule to the connected ESP32 via serial_rpc.

Usage: TRANSIT_TRACKER_TESTING=1 uv run scripts/flash_device_config.py /dev/cu.usbmodem1101
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from transit_tracker.config import TransitConfig
from transit_tracker.hardware import flash_hardware

if len(sys.argv) < 2:
    print("Usage: flash_device_config.py <serial_port>", file=sys.stderr)
    sys.exit(2)

port = sys.argv[1]
config = TransitConfig.load()
# Use the LAN hostname the device was using before
config.api_url = "ws://Tommys-Mac-mini.local:8000/"
ok = flash_hardware(port, config)
sys.exit(0 if ok else 1)
