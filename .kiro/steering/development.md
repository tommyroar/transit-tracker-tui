---
inclusion: auto
---

# Development Workflow

## Environments

There are two environments. Do not conflate them.

### Local (Development)

Run the Python server directly for development and testing:

```
uv run transit-tracker service    # WebSocket proxy :8000 + web :8080 + GUI tray
uv run transit-tracker web        # HTTP web server only
uv run transit-tracker simulator  # TUI LED matrix simulator (UAT visual check)
```

The TUI simulator (`transit-tracker simulator`) is the primary UAT tool during development — it renders the LED matrix output in the terminal so you can visually verify trip formatting, colors, and layout without the physical board. This will eventually be replaced by the web simulator (`/simulator` endpoint) running inside the configurator local webapp.

Jupyter notebooks live alongside scripts for data processing tasks (walkshed generation, GTFS analysis, route mapping). Use Playwright for browser-based validation of web endpoints (`/walkshed`, `/simulator`, `/spec`).

All code changes are tested here first. Run `uv run pytest -v -m "not docker"` before pushing.

### Container (Production)

The Docker container (`transit-tracker:latest`) is the live production deployment. It runs with `--restart=always` via OrbStack and auto-starts on login.

- Ports: 8000 (WS), 8081→8080 (HTTP)
- Config: `.local/home.yaml` mounted at `/config/config.yaml`
- Status: `curl http://localhost:8081/api/status`
- Logs: `docker logs transit-tracker`

**Do not rebuild, restart, or touch the production container unless explicitly instructed.** It is serving the physical transit board and connected ESP32 hardware.

The container is the final UAT phase in a spec job. After all tasks pass locally, the last step is building a new image, swapping the container, and verifying on the live board.

## Spec Task Lifecycle

1. Implement and test locally (Python server, pytest, Playwright)
2. All non-docker tests pass
3. Run TUI simulator (`transit-tracker simulator`) for visual UAT during development
4. When instructed: rebuild image, recreate container, verify on live hardware
5. UAT confirmation from user completes the spec

## Tools

| Tool | Purpose |
|------|---------|
| `uv run pytest` | Unit/integration tests (exclude `docker` marker for local) |
| `uv run pytest -m docker` | Container integration tests (ports 18000/18080) |
| Jupyter notebooks | Data processing, walkshed generation, GTFS analysis |
| Playwright | Browser validation of web endpoints |
| `scripts/verify_cloud_equivalence.py` | Compare local/container against reference |
| `scripts/start_container.sh --detach` | Start production container |
| `scripts/stop_container.sh` | Stop production container |

## Service Management

The CLI manages the service via Docker first, launchctl fallback:

```
transit-tracker service start    # docker start transit-tracker
transit-tracker service stop     # docker stop transit-tracker
transit-tracker service restart  # docker restart transit-tracker
transit-tracker service status   # docker inspect state
```
