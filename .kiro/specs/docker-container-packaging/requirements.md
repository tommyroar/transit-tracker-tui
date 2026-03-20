# Requirements Document

## Introduction

This feature packages the Transit Tracker Python web server and WebSocket proxy as a Docker container that is API-compatible with the reference `ghcr.io/tjhorner/transit-tracker-api` container. The goal is to produce a drop-in replacement container that serves the same WebSocket protocol and HTTP endpoints, while adding extended functionality such as Washington State Ferries support, route abbreviations, and vessel name mapping. The container must be testable against the reference container to verify output equivalence for a shared configuration.

## Glossary

- **Transit_Container**: The Docker container built from this project's Dockerfile, packaging the Transit Tracker Python service
- **Reference_Container**: The official `ghcr.io/tjhorner/transit-tracker-api` Docker image maintained by tjhorner
- **Equivalence_Test**: An automated script that compares WebSocket output from the Transit_Container and Reference_Container for a shared configuration and asserts structural and data parity
- **Steering_File**: A Kiro steering document (`.kiro/steering/`) that provides guidance to AI agents about container conventions
- **Container_Test**: A pytest test that validates the Transit_Container builds, starts, exposes correct ports, and responds correctly
- **Start_Script**: A shell script (`scripts/start_container.sh`) that builds and runs the Transit_Container
- **Stop_Script**: A shell script (`scripts/stop_container.sh`) that stops and removes the Transit_Container
- **Config_Volume**: A Docker volume mount that maps a host config YAML file into the Transit_Container at runtime

## Requirements

### Requirement 1: Dockerfile Definition

**User Story:** As a developer, I want a Dockerfile that packages the Transit Tracker Python service, so that I can deploy it as a portable container.

#### Acceptance Criteria

1. THE Transit_Container SHALL be built from a multi-stage Dockerfile using a Python 3.14 base image and `uv` for dependency installation
2. WHEN the Dockerfile is built, THE Transit_Container SHALL include only runtime dependencies and exclude development dependencies, test files, and build tools
3. THE Transit_Container SHALL expose port 8000 for the WebSocket server and port 8080 for the HTTP web server
4. THE Transit_Container SHALL use a non-root user to run the service process
5. WHEN no Config_Volume is mounted, THE Transit_Container SHALL start with a default configuration that connects to the public API at `wss://tt.horner.tj/`
6. WHEN a Config_Volume is mounted at `/config/config.yaml`, THE Transit_Container SHALL load that configuration file at startup

### Requirement 2: API Compatibility with Reference Container

**User Story:** As a developer, I want the Transit_Container to be API-compatible with the Reference_Container, so that existing clients (ESP32 hardware, web simulators) can connect without modification.

#### Acceptance Criteria

1. THE Transit_Container SHALL accept `schedule:subscribe` WebSocket messages in the same format as the Reference_Container
2. THE Transit_Container SHALL emit `schedule` WebSocket messages with the same JSON schema as the Reference_Container, including all fields: `tripId`, `routeId`, `routeName`, `stopId`, `stopName`, `headsign`, `arrivalTime`, `departureTime`, `isRealtime`
3. THE Transit_Container SHALL emit `heartbeat` WebSocket messages every 10 seconds
4. WHEN a client sends an empty `routeStopPairs` string, THE Transit_Container SHALL use its own configuration for subscriptions, matching Reference_Container behavior
5. THE Transit_Container SHALL serve the OpenAPI specification at `/openapi` as JSON, matching the Reference_Container's endpoint

### Requirement 3: Reference Container Download and Comparison

**User Story:** As a developer, I want to pull the Reference_Container and compare its output against the Transit_Container, so that I can verify protocol equivalence.

#### Acceptance Criteria

1. THE Equivalence_Test SHALL pull the Reference_Container image `ghcr.io/tjhorner/transit-tracker-api` if it is not already present locally
2. THE Equivalence_Test SHALL start both the Reference_Container and Transit_Container with a shared reference configuration containing at least two route/stop pairs
3. WHEN both containers are running, THE Equivalence_Test SHALL connect to each via WebSocket, send identical `schedule:subscribe` messages, and compare the resulting `schedule` event schemas
4. THE Equivalence_Test SHALL verify that the top-level response keys and per-trip field names match exactly between both containers
5. IF the Reference_Container fails to respond within 120 seconds, THEN THE Equivalence_Test SHALL report a timeout error and skip data comparison
6. THE Equivalence_Test SHALL produce a human-readable report showing trip counts, field-level matches, and any schema divergences

### Requirement 4: Start and Stop Scripts

**User Story:** As a developer, I want simple scripts to start and stop the Transit_Container, so that I can manage the container lifecycle without memorizing Docker commands.

#### Acceptance Criteria

1. THE Start_Script SHALL build the Transit_Container image from the project Dockerfile if no image exists locally
2. THE Start_Script SHALL start the Transit_Container with port mappings for WebSocket (8000) and HTTP (8080) and mount the local config file as a Config_Volume
3. THE Start_Script SHALL wait for the Transit_Container to accept WebSocket connections before reporting success
4. THE Stop_Script SHALL stop the running Transit_Container and remove the stopped container
5. IF the Transit_Container is not running when the Stop_Script is executed, THEN THE Stop_Script SHALL exit cleanly with a message indicating no container was found
6. THE Start_Script SHALL accept an optional `--detach` flag to run the container in the background

### Requirement 5: Container Tests

**User Story:** As a developer, I want automated tests that validate the Transit_Container builds and runs correctly, so that container regressions are caught in CI.

#### Acceptance Criteria

1. THE Container_Test SHALL verify that the Dockerfile builds without errors
2. THE Container_Test SHALL verify that the Transit_Container starts and accepts WebSocket connections within 60 seconds
3. THE Container_Test SHALL verify that a WebSocket client can connect to port 8000 and receive a `heartbeat` message
4. THE Container_Test SHALL verify that the HTTP server returns a valid JSON response at `/openapi`
5. THE Container_Test SHALL clean up all created containers and images after test completion

### Requirement 6: Steering Files

**User Story:** As a developer, I want Kiro steering files that document container conventions, so that AI agents can assist with container-related tasks correctly.

#### Acceptance Criteria

1. THE Steering_File SHALL be created at `.kiro/steering/docker.md` with auto-inclusion
2. THE Steering_File SHALL document the Dockerfile location, image naming convention, port mappings, and config volume mount path
3. THE Steering_File SHALL document the start and stop script locations and usage
4. THE Steering_File SHALL document the container test file location and how to run container tests

### Requirement 7: Extended Functionality Preservation

**User Story:** As a developer, I want the Transit_Container to preserve all extended functionality beyond the Reference_Container, so that ferry support and other custom features remain available.

#### Acceptance Criteria

1. THE Transit_Container SHALL support Washington State Ferries (Agency 95) with vessel name mapping and route abbreviations
2. THE Transit_Container SHALL support the `wsf:` and `st:` ID prefix conventions for stop and route identifiers
3. THE Transit_Container SHALL support per-trip arrival/departure mode selection based on OBA `arrivalEnabled`/`departureEnabled` flags
4. THE Transit_Container SHALL serve the web LED simulator, API spec page, and station map at their existing HTTP paths
5. WHEN the Transit_Container is configured with ferry subscriptions, THE Transit_Container SHALL include ferry trips in the `schedule` WebSocket output with vessel names as headsigns for live-tracked ferries
