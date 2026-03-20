# Implementation Plan: Docker Container Packaging

## Overview

Package the Transit Tracker Python web server and WebSocket proxy into a single Docker container that is API-compatible with the reference `ghcr.io/tjhorner/transit-tracker-api` image. Implementation proceeds bottom-up: health endpoint first, then Dockerfile and entrypoint, lifecycle scripts, container tests, equivalence tests, and finally the steering file.

## Tasks

- [x] 1. Write property tests for existing WebSocket and config behavior
  - [ ]* 1.1 Write property test for schedule response schema completeness
    - **Property 1: Schedule response schema completeness**
    - Generate random trip data, pass through `send_update` formatting, verify output trip objects always contain exactly the 9 required fields: `tripId`, `routeId`, `routeName`, `stopId`, `stopName`, `headsign`, `arrivalTime`, `departureTime`, `isRealtime`
    - **Validates: Requirements 2.2**

  - [ ]* 1.2 Write property test for ID prefix normalization round-trip
    - **Property 4: ID prefix normalization round-trip**
    - Generate random prefixed IDs (`st:`, `wsf:`, bare), verify `normalize_id` produces valid OBA format and `stopId` in output preserves the original prefix
    - **Validates: Requirements 7.2**

  - [ ]* 1.3 Write property test for arrival/departure mode selection
    - **Property 5: Arrival/departure mode selection**
    - Generate random combinations of `arrivalEnabled`/`departureEnabled` flags with arrival and departure timestamps, verify the correct time is selected per the design rules
    - **Validates: Requirements 7.3**

  - [ ]* 1.4 Write property test for ferry vessel name mapping
    - **Property 6: Ferry vessel name mapping**
    - Generate random ferry trips with and without `vehicleId`, verify headsign is vessel name when vehicleId is present and original headsign when absent
    - **Validates: Requirements 7.5**

- [x] 2. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Create Dockerfile and entrypoint script
  - [x] 3.1 Create `Dockerfile` at the project root
    - Multi-stage build: build stage uses `python:3.14-slim` + `uv` to install production deps; runtime stage copies venv + source
    - Create non-root `transit` user (UID 1000)
    - `EXPOSE 8000 8080`
    - Exclude tests, scripts, dev deps, `.git`, `data/gtfs` via `.dockerignore`
    - _Requirements: 1.1, 1.2, 1.3, 1.4_

  - [x] 3.2 Create `.dockerignore` file
    - Exclude `.git`, `tests`, `scripts`, `data/gtfs`, `.venv`, `__pycache__`, `.ruff_cache`, `.pytest_cache`, dev config files
    - _Requirements: 1.2_

  - [x] 3.3 Create `docker/entrypoint.sh`
    - Check for `/config/config.yaml` and pass to services if mounted
    - Start WebSocket service (`python -m transit_tracker.cli service`) in background
    - Start HTTP web server (`python -m transit_tracker.cli web`) in background
    - Set `auto_launch_gui: false` behavior (skip GUI in container)
    - Wait for both processes; exit if either dies
    - _Requirements: 1.5, 1.6, 8.4_

- [x] 4. Create start and stop lifecycle scripts
  - [x] 4.1 Create `scripts/start_container.sh`
    - Build image if not present (`docker build -t transit-tracker .`)
    - Run container with `-p 8000:8000 -p 8080:8080`
    - Mount config: `-v $(pwd)/.local/home.yaml:/config/config.yaml:ro` (default, override with `--config <path>`)
    - Support `--detach` flag for background mode; wait for WebSocket port 8000 to accept connections (up to 60s) when detached
    - Without `--detach`: run in foreground with `--rm`
    - _Requirements: 4.1, 4.2, 4.3, 4.6_

  - [x] 4.2 Create `scripts/stop_container.sh`
    - Stop the `transit-tracker` container via `docker stop`
    - Remove the stopped container via `docker rm`
    - Exit cleanly with message if no container is running
    - _Requirements: 4.4, 4.5_

- [x] 5. Checkpoint - Verify Dockerfile builds and scripts work
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Create container integration tests
  - [x] 6.1 Create `tests/test_container.py` with pytest-based container tests
    - Build the Docker image
    - Start the container, verify WebSocket connection on port 8000 receives a `heartbeat` within 60s
    - Verify `/openapi` returns valid JSON
    - Clean up containers and images after completion
    - Mark tests with `@pytest.mark.docker` for selective execution
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5_

  - [ ]* 6.2 Write property test for subscribe message acceptance
    - **Property 2: Subscribe message acceptance**
    - Generate random valid `routeStopPairs` strings, send to server, verify no error and a schedule event is returned
    - **Validates: Requirements 2.1**

- [x] 7. Extend equivalence test for container-vs-container comparison
  - [x] 7.1 Extend `scripts/verify_cloud_equivalence.py` for container comparison
    - Pull `ghcr.io/tjhorner/transit-tracker-api` if not present
    - Start both containers with shared reference config (`.local/home.yaml` with at least 2 route/stop pairs)
    - Connect to each via WebSocket, send identical `schedule:subscribe` messages
    - Compare top-level response keys and per-trip field names
    - Produce human-readable report with trip counts, field matches, divergences
    - 120-second timeout with skip-on-timeout behavior
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ]* 7.2 Write property test for schema equivalence with reference container
    - **Property 3: Schema equivalence with reference container**
    - Integration property: requires both containers running. Generate shared configs, compare response schemas
    - Mark with `@pytest.mark.integration`
    - **Validates: Requirements 3.3, 3.4**

- [x] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 9. Create Kiro steering file for Docker conventions
  - [x] 9.1 Create `.kiro/steering/docker.md` with auto-inclusion
    - Document Dockerfile location and image naming convention (`transit-tracker:latest`)
    - Document port mappings (8000 WS, 8080 HTTP)
    - Document config volume mount path (`/config/config.yaml`)
    - Document start/stop script locations and usage
    - Document container test file location and how to run container tests
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

- [x] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Replace Nomad raw_exec deployment with Docker container for live UAT
  - [x] 11.1 Update `transit-tracker.nomad.hcl` to use Docker driver instead of `raw_exec`
    - Replace the two `raw_exec` tasks (`websocket-server` and `web-server`) with a single `docker` driver task running the `transit-tracker:latest` image
    - Map ports: `ws` static 8000 → container 8000, `web` static 8081 → container 8080
    - Mount the board subscription config as a volume: host `.local/home.yaml` → container `/config/config.yaml:ro`
    - Preserve restart policy (3 attempts, 5m interval, 10s delay)
  - [x] 11.2 Stop the current Nomad job and redeploy with the container
    - Run `nomad job stop transit-tracker` to stop the existing raw_exec processes
    - Run `nomad job run transit-tracker.nomad.hcl` to deploy the container version
    - Verify the container starts and WebSocket port 8000 accepts connections
    - Verify the live ESP32 hardware connects and displays schedule data
    - Ask the user to confirm UAT passes on the physical transit board

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Container tests require Docker and are marked with `@pytest.mark.docker` for selective execution
- Equivalence tests require network access and are marked with `@pytest.mark.integration`
