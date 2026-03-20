---
inclusion: auto
---

# Docker Container Conventions

## Image

- Dockerfile: `Dockerfile` (project root)
- Image name: `transit-tracker:latest`
- Base: `python:3.14-slim` (multi-stage build with `uv`)
- Non-root user: `transit` (UID 1000)

## Ports

| Service          | Container Port | Protocol |
|------------------|---------------|----------|
| WebSocket server | 8000          | WS       |
| HTTP web server  | 8080          | HTTP     |

## Config

- Mount a board config YAML at `/config/config.yaml` to override defaults
- Without a mount, the container connects to `wss://tt.horner.tj/` (public API)
- Example: `-v $(pwd)/.local/home.yaml:/config/config.yaml:ro`

## Lifecycle Scripts

- Start: `scripts/start_container.sh [--detach] [--config <path>]`
- Stop: `scripts/stop_container.sh`
- Default config: `.local/home.yaml`

## Container Tests

- File: `tests/test_container.py`
- Marker: `@pytest.mark.docker`
- Run: `pytest -m docker tests/test_container.py`
- Uses unique ports (18000/18080) to avoid conflicts

## Equivalence Testing

- Script: `python scripts/verify_cloud_equivalence.py --containers`
- Compares local container against reference `ghcr.io/tjhorner/transit-tracker-api`
- Reference container runs on port 3000 (single port for WS + HTTP)
