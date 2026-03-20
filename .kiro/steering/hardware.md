---
inclusion: auto
---

# Hardware Configuration — Home Network

## ESP32 Device

- Device name: `transit-tracker-ec7db4`
- Chip: ESP32-S3 (dual-core, rev 2, 240 MHz, 8MB flash, DIO mode)
- EFuse MAC: `28:37:2F:EC:7D:B4`
- Framework: Arduino (ESP-IDF 5.5.2)
- ESPHome version: 2026.2.1
- Firmware: v2.8.2 (v2.8.3 update available)
- IP address: `192.168.5.248` (static/DHCP)
- Wi-Fi signal: -38 dBm (excellent)
- Display: HUB75 RGB LED matrix, 2 panels × 64×32 (128×32 total), 2.5mm pitch
- Driver board: Adafruit ESP32-S3 Matrix Portal

## Host Machine

- Hostname: `Tommys-Mac-mini.local`
- IP: `192.168.5.232` (Ethernet, en0, 1000baseT full-duplex)
- Network: `192.168.4.0/22` (broadcast `192.168.7.255`)
- The ESP32 connects to the local proxy via `ws://Tommys-Mac-mini.local:8000/`

## Local WebSocket Proxy

- Runs on port 8000 (python3.1, PID varies)
- The ESP32 maintains a persistent WebSocket connection to this host
- Active connection visible in `lsof -i :8000` as `192.168.5.232:8000 <-> 192.168.5.248`
- No launchd plist registered — service is started manually or from the TUI

## Current Device Settings (live)

- `base_url`: `ws://Tommys-Mac-mini.local:8000/`
- `schedule_config`: empty (server-side config drives subscriptions)
- `time_display`: `arrival`
- `time_units`: `short` (e.g., "5m")
- `list_mode`: `sequential`
- `scroll_headsigns`: ON
- `display_brightness`: OFF (panel-level, not software)
- `now_str`: "Now", `min_long_str`: "min", `min_short_str`: "m", `hours_short_str`: "h"

## Active Config Profile

The primary config is `.local/accurate_config.yaml` with `use_local_api: true`. Subscriptions:

| Stop ID | Label | Route | Walk Offset |
|:---|:---|:---|:---|
| `st:1_8494` | Restored Stop | `st:40_100240` (554) | -7min |
| `st:1_11920` | Restored Stop | `st:1_100039` (271) | -9min |
| `st:95_7` | Seattle Terminal | `st:95_73` (SEA-BI ferry) | 0 |
| `st:95_3` | Bainbridge Terminal | `st:95_73` (SEA-BI ferry) | 0 |

Ferry abbreviations configured: SEA/BAI, BAI/SEA, BRE/SEA, SEA/BRE.

## Other Profiles

- `.local/home.yaml` — bus-only (554, 271), local API, scroll ON
- `.local/adventure.yaml` — buses + ferries, departure mode, full WSF abbreviation set
- `.local/needle_stops.yaml` — Space Needle area stops, cloud API, long units

## Development Notes

- The device's web UI is at `http://192.168.5.248/` (ESPHome native dashboard)
- Entity state is available via REST: `http://192.168.5.248/text/<entity_id>`, `/select/<entity_id>`, `/switch/<entity_id>`
- SSE event stream at `http://192.168.5.248/events` provides real-time logs and state changes
- Serial JSON-RPC (`JRPC:` prefix) is used for USB configuration — see `hardware.py` and `capture_hardware.py`
- The `flip_display` entity does not exist on this firmware version
