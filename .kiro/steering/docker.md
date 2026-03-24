---
inclusion: auto
---

# Docker Container Conventions

## Production Deployment

The container is the live production deployment serving the physical transit board. It runs with `--restart=always` via OrbStack and auto-starts on macOS login.

**Do not rebuild or restart the container unless explicitly instructed.**

```
docker ps                                    # check status
curl http://localhost:8081/api/status         # live service state
docker logs transit-tracker --tail 20        # recent logs
```

## Image

- Dockerfile: `Dockerfile` (project root)
- Image name: `transit-tracker:latest`
- Base: `python:3.14-slim` (multi-stage build with `uv`)
- Non-root user: `transit` (UID 1000)

## Ports

| Service          | Container Port | Host Port | Protocol |
|------------------|---------------|-----------|----------|
| WebSocket server | 8000          | 8000      | WS       |
| HTTP web server  | 8080          | 8081      | HTTP     |

## Config

- Mount a board config YAML at `/config/config.yaml` to override defaults
- Without a mount, the container connects to `wss://tt.horner.tj/` (public API)
- Production config: `.local/home.yaml`

## GTFS Static Schedule

Mount the GTFS SQLite index to enable scheduled trip merging alongside live data:

```
-v $(pwd)/data/gtfs_index.sqlite:/data/gtfs/gtfs_index.sqlite:ro
```

- **With mount**: boards receive GTFS scheduled trips immediately on subscribe and as gap-fill between live updates. Live trips always supersede scheduled trips for the same `tripId`.
- **Without mount**: only live OBA data is sent (identical to previous behavior).
- Build the index: `uv run python scripts/download_gtfs.py`
- The `start_container.sh` script auto-detects the file and adds the mount if present.

## Container Management

Managed directly by Docker restart policy:

```
docker run -d --name transit-tracker --restart=always \
  -p 8000:8000 -p 8081:8080 \
  -v $(pwd)/.local/home.yaml:/config/config.yaml:ro \
  -v $(pwd)/data/gtfs_index.sqlite:/data/gtfs/gtfs_index.sqlite:ro \
  transit-tracker:latest
```

Lifecycle scripts:
- Start: `scripts/start_container.sh --detach [--config <path>]`
- Stop: `scripts/stop_container.sh`

CLI integration:
- `transit-tracker service start|stop|restart|status` talks to Docker directly

## Container Tests

- File: `tests/test_container.py`
- Marker: `@pytest.mark.docker`
- Run: `pytest -m docker tests/test_container.py`
- Uses unique ports (18000/18080) to avoid conflicts with production

## Equivalence Testing

- Script: `python scripts/verify_cloud_equivalence.py --containers`
- Compares local container against reference `ghcr.io/tjhorner/transit-tracker-api`
- Reference container runs on port 3000 (single port for WS + HTTP)

## Boot Chain

```
macOS login → OrbStack (start_at_login) → container (restart=always)
```
