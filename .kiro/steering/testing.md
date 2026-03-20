---
inclusion: auto
---

# Testing Approach

## Framework & Config

- pytest with pytest-asyncio (strict mode)
- Config in `pyproject.toml`: `python.testing.pytestArgs: ["tests"]`
- Run all: `uv run pytest tests/ -v`
- Run one file: `uv run pytest tests/test_station_map.py -v`
- CI script: `./scripts/ci_local.sh`

## Patterns

### Fixtures
- Use `@pytest.fixture` for reusable test data (configs, stops, GTFS trees)
- `TransitConfig(transit_tracker={...})` for config fixtures — always use the nested form
- `tmp_path` for file-system-dependent tests (GTFS CSVs, SQLite DBs, GeoJSON)
- `MagicMock` / `AsyncMock` for API clients and external services

### Mocking
- `unittest.mock.patch` for module-level constants (`_GTFS_DIR`, `_GTFS_DB`, `_PROJECT_ROOT`)
- `AsyncMock(spec=TransitAPI)` for OBA API calls
- Never hit real APIs in tests — all network calls are mocked
- For GTFS file tests, create real CSV files in `tmp_path` rather than mocking file reads

### HTTP Handler Tests
- Use the `_make_handler(method, path)` helper pattern from `test_web.py`
- Creates a `MockHandler` subclass that captures status code, body, headers
- Set `TransitWebHandler.routes` before calling `_make_handler`

### Test Organization
- Group related tests in classes (`TestIndexMap`, `TestRoutePolylines`, etc.)
- Each class tests one function or feature area
- Docstrings on every test explaining what it verifies

### Naming
- Files: `tests/test_{feature}.py`
- Classes: `Test{Feature}`
- Functions: `test_{what_it_verifies}`

## Coverage Map

| Module | Test File | What's Covered |
|--------|-----------|----------------|
| `web.py` — stop resolution | `test_web.py` | OBA API calls, error handling, None responses |
| `web.py` — API spec | `test_web.py` | JSON validity, structure, config-derived examples |
| `web.py` — index map | `test_station_map.py` | Leaflet rendering, WSF stops, bounds, modal, GeoJSON fetch |
| `web.py` — walkshed HTML | `test_station_map.py` | Leaflet, stop embedding, walk radius, route polylines |
| `web.py` — route polylines | `test_station_map.py` | GTFS shape resolution, prefix stripping, dedup, missing routes |
| `web.py` — stations GeoJSON | `test_station_map.py` | File loading, missing file fallback, schema |
| `web.py` — HTTP handler | `test_web.py`, `test_station_map.py` | Routing, 404s, content types, CORS, GeoJSON MIME |
| `gtfs_schedule.py` | `test_gtfs.py` | Service IDs, departures, prefix stripping, wake-up, ferry fallback |
| `websocket_server.py` | `test_network.py` | Subscriptions, caching, rate limiting, spacing, backoff |
| `config.py` | `test_config.py` | Parsing, validation, sync, profiles |
| `scripts/build_stations_geojson.py` | `test_station_map.py` | Station dedup, coordinate validation |

## What's NOT Tested (and why)

- `generate_simulator_html()` — large JS template, tested manually via browser
- `run_web()` — integration function that starts HTTPServer, tested via `scripts/verify_launch.py`
- `scripts/build_route_map.py` — one-shot build script, output validated visually
- `scripts/download_gtfs.py` — network-dependent download, tested via CI with real GTFS feeds
- Mapbox isochrone fetch in `build_stations_geojson.py` — requires API token, tested manually
