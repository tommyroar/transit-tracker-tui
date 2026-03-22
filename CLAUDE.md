# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Transit Tracker is a macOS background daemon that proxies OneBusAway API data to ESP32 LED matrix hardware via WebSocket. It supports both cloud relay mode (via `wss://tt.horner.tj/`) and local self-hosted mode.

## Commands

### Run tests
```bash
uv run pytest -v                                  # all tests
uv run pytest tests/test_config.py                # single file
uv run pytest tests/test_config.py::test_load_config  # single test
```

### Lint and format
```bash
uv run ruff check .
uv run ruff format .
```

### Full CI pipeline
```bash
./scripts/ci_local.sh   # sync deps → test → verify launch → build
```

### GTFS static schedule (optional, enables offline/wake-up fallback)
```bash
uv run python scripts/download_gtfs.py   # downloads ~200-400 MB; run once after cloning
```
Downloads GTFS feeds for King County Metro, Sound Transit Rail, and Washington State Ferries,
then builds `data/gtfs_index.sqlite`. When the DB exists, `TransitServer` serves scheduled
trips immediately on client connect (before the OBA cache is warm).

### Install / run
```bash
uv tool install .       # installs `transit-tracker` CLI to PATH
transit-tracker         # launch TUI
transit-tracker service # start background service (WebSocket server + client)
```

## Architecture

### Two operating modes

**Cloud mode** (`use_local_api: false`, default): The ESP32 connects directly to `wss://tt.horner.tj/`; this app acts as a configurator only.

**Local mode** (`use_local_api: true`): This app runs a full WebSocket server on `:8000`. ESP32 connects locally; the service fetches OBA data and pushes updates.

### Core data flow (local mode)
1. ESP32 sends `schedule:subscribe` with stop/route pairs to `TransitServer` (`:8000`)
2. `data_refresh_loop` polls OBA API on `check_interval_seconds` (default 30s)
3. Server applies: ferry vessel name mapping, route abbreviations, time offset, arrival filtering
4. `broadcast_loop` pushes JSON updates to all connected clients
5. ESP32 firmware computes `display_mins = (json["arrivalTime"] - now) / 60`

### Key modules

| Module | Role |
|--------|------|
| `config.py` | Two-part config: `ServiceSettings` (service/env) + `TransitTrackerSettings` (board subscriptions), merged into `TransitConfig` at runtime |
| `transit_api.py` | Async httpx client for OBA API (geocode, stops, arrivals, polylines) |
| `network/websocket_server.py` | Local proxy — subscriptions, OBA polling, rate-limit backoff, ferry logic |
| `network/websocket_service.py` | Background client — connects to configured API endpoint, monitors config changes |
| `tui.py` | `rich`/`questionary` interactive configurator (1,019 lines) |
| `simulator.py` | 64×32 LED matrix emulator with BDF fonts |
| `cli.py` | Entry point; `manage_service()` uses `launchctl` for daemon lifecycle |
| `gui.py` | macOS tray icon via `rumps`; text simulator in profile submenu; WS bootstrap on startup |

### Ferry support
- Use `wsf:` prefix for stop/route IDs (e.g., `wsf:7` = Seattle terminal)
- `WSF_VESSELS` dict in `websocket_server.py` maps `vehicleId` → vessel name (e.g., `"95_28"` → `"Sealth"`)
- Vessel name replaces headsign only when `vehicleId` is present in OBA realtime data
- OBA `arrivalEnabled`/`departureEnabled` per-trip flags determine whether to show arrival or departure time (origin docks show departure, destination docks show arrival)

### Rate limiting
`TransitServer` tracks per-stop `rate_limit_until` timestamps. On 429: interval doubles (cap 600s). On recovery: interval reduces 20% per successful fetch. Throttle metrics (`throttle_total`, `api_calls_total`, `throttle_rate`) are synced to `service_state.json` and shown in the GUI dropdown. Per-event JSONL log at `~/.config/transit-tracker/throttle_log.jsonl`.

### GUI text simulator
The menu bar profile submenu shows live trip data as text lines: `ROUTE  Headsign  ◉ Xm` (◉ = realtime, ○ = scheduled). The `format_trip_line()` function in `gui.py` handles formatting. On startup, the GUI connects to `ws://localhost:8000` to fetch immediate data rather than waiting for state file polling.

### GUI lifecycle
The GUI tray icon is a subprocess of the service (`run_full_service()` in `cli.py`). When the service restarts, the GUI is killed and relaunched automatically. `service restart` uses `launchctl kickstart -k` for reliability.

### Config (two-file system)
- **Board subscription profiles** (`.local/*.yaml`, `data/needle_stops.yaml`): Pure stop/route data under `transit_tracker:` key. Schema: `TransitTrackerSettings` — only `base_url`, `time_display`, `scroll_headsigns`, `display_format`, `stops`, `abbreviations`. No API keys or service settings.
- **Service settings** (`.local/service.yaml`, gitignored): `ServiceSettings` model — `oba_api_key`, `check_interval_seconds`, `request_spacing_ms`, `arrival_threshold_minutes`, `num_panels`, `panel_width`, `panel_height`, `use_local_api`, `auto_launch_gui`, `last_config_path`.
- `TransitConfig` is a runtime composite: merges both at load time. Access board settings via `config.transit_tracker.*`, service settings via `config.service.*`.
- `config.save()` writes only the `transit_tracker:` block. Service settings persist via `save_service_settings()`.
- Profile `.yaml` files can live in project root or `.local/`

### Integration testing
`tests/test_cloud_equivalence.py` connects to both `wss://tt.horner.tj` (cloud) and `ws://localhost:8000` (local) with identical subscriptions and compares response schema, trip field types, sort order, and route metadata. Marked `e2e` — excluded from CI via `-m "not e2e"`. Run with `uv run pytest -m e2e` to exercise live.

## Service management

The service runs as a Docker container with `--restart=always`. See `.kiro/steering/docker.md` for full container context.

```bash
transit-tracker service start     # docker start transit-tracker
transit-tracker service stop      # docker stop transit-tracker
transit-tracker service restart   # docker restart transit-tracker
transit-tracker service status    # docker inspect state
```
