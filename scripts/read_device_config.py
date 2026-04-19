#!/usr/bin/env python3
"""Read base_url / schedule from the connected ESP32 via serial_rpc."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from transit_tracker.hardware import ESPHomeFlasher, EntityType

if len(sys.argv) < 2:
    print("Usage: read_device_config.py <serial_port>", file=sys.stderr)
    sys.exit(2)

port = sys.argv[1]
with ESPHomeFlasher(port) as flasher:
    for name in ("base_url_config", "schedule_config", "time_display_config"):
        try:
            result = flasher.get_entity(name, EntityType.TEXT if name != "time_display_config" else EntityType.SELECT)
            print(f"{name}: {result!r}")
        except Exception as e:
            print(f"{name}: ERROR {e}")
