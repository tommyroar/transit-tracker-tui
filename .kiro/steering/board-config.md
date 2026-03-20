---
inclusion: auto
---

# Transit Board Configuration

## What Config Files Are

Config files are subscription configurations for the transit board hardware (ESP32 LED matrix displays). They define which stops and routes the board displays, not how the service itself runs. The same config should work whether connected to the reference container (`ghcr.io/tjhorner/transit-tracker-api`) or this project's custom container.

## Running Services

The transit tracker runs as a Docker container in production, or as local Python processes in development:

**Production (container):**
- `docker ps` — check container status
- `transit-tracker service [start|stop|restart|status]` — manages the Docker container
- Container runs WebSocket (:8000) + HTTP (:8080, mapped to host :8081)
- `--restart=always` via OrbStack, auto-starts on login

**Development (local Python):**
- `uv run transit-tracker service` — WebSocket proxy + web server + GUI tray
- `uv run transit-tracker web` — HTTP web server only
- `transit-tracker gui` — macOS tray icon (auto-launched by service when `auto_launch_gui: true`)

## Config Profiles (`.local/` directory)

- `.local/home.yaml` — standard board config: two Sound Transit stops, public API, no extras. Valid for both reference and custom containers.
- `.local/adventure.yaml` — extended board config: ferries + Sound Transit + abbreviations. Only works with the custom container.
- `.local/needle_stops.yaml` — minimal board config: two Space Needle area stops
- `.local/config.yaml` — empty/default (no stops subscribed)
- `.local/accurate_config.yaml` — reference config for accuracy testing
- `.local/test_isolation_config.yaml` — test-only config

## Config Structure

All configs use the `TransitConfig` Pydantic model. The canonical form nests board subscriptions under `transit_tracker:`:

```yaml
transit_tracker:
  base_url: "wss://tt.horner.tj/"
  time_display: arrival
  check_interval_seconds: 30
  stops:
    - stop_id: "st:1_8494"
      time_offset: "-7min"
      routes:
        - "st:40_100240"
  abbreviations: []
  styles: []
```

## Config Loading Order

1. Check `get_last_config_path()` from `~/.config/transit-tracker/settings.yaml`
2. Check explicit path argument
3. Check `.local/<path>` fallback
4. Default to `TransitConfig()` (public API at `wss://tt.horner.tj/`)

## ID Prefix Conventions

- `st:` — Sound Transit / OneBusAway (e.g., `st:1_8494`, `st:40_100240`)
- `wsf:` — Washington State Ferries (e.g., `wsf:7`, `wsf:73`) — custom container only
- Bare numbers — raw OBA agency_id format

## Port Assignments

| Service | Port | Protocol |
|---------|------|----------|
| WebSocket server | 8000 | WS |
| HTTP web server | 8080 | HTTP |

## Key Environment Variables

- `TRANSIT_TRACKER_TESTING=1` — disables config persistence, enables test isolation
- `PORT` — override HTTP web server port (default: 8080)
