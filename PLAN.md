# 🏙️ Transit Notification Service Plan

This document outlines the design for a lightweight Python service that monitors public transit arrivals using the **Transit Tracker API** and sends push notifications via **ntfy.sh**. It also provides options for self-hosting the entire stack.

## 🚀 Overview

The service will maintain a persistent WebSocket connection to the Transit Tracker API (hosted or self-hosted), subscribe to specific stop/route pairs, and trigger a notification when a bus or train is within a user-defined arrival threshold (e.g., 5 minutes).

## 🏠 Hosting Options

### Option A: Using Hosted API (Fastest)
- **API URL:** `wss://tt.horner.tj`
- **Pros:** No server maintenance, works immediately.
- **Cons:** Dependent on external service uptime.

### Option B: Self-Hosting API via Docker (Recommended for Mac Mini)
You can run your own instance of the Transit Tracker API on your Mac Mini. This allows you to add custom feeds or ensure 100% uptime for your commute.

**Docker Compose Configuration (`docker-compose.yml`):**
```yaml
services:
  api:
    image: ghcr.io/tjhorner/transit-tracker-api:main
    depends_on:
      - postgres
      - redis
    environment:
      REDIS_URL: "redis://redis:6379"
      DATABASE_URL: "postgres://postgres:postgres@postgres:5432/gtfs?sslmode=disable"
      FEED_SYNC_SCHEDULE: "0 0 * * *"
      FEEDS_CONFIG: |
        feeds:
          st:
            name: Sound Transit
            description: Puget Sound, WA
            gtfs:
              static:
                url: https://www.soundtransit.org/google_transit.zip
              rtTripUpdates:
                url: https://api.soundtransit.org/external-api/gtfs-rt/trip-updates.pb
          kcm:
            name: King County Metro
            description: King County, WA
            gtfs:
              static:
                url: https://metro.kingcounty.gov/GTFS/google_transit.zip
              rtTripUpdates:
                url: https://s3.amazonaws.com/kcm-alerts-realtime-prod/tripupdates.pb
    ports:
      - "3000:3000"

  postgres:
    image: postgres:17
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: gtfs
    volumes:
      - "db_data:/var/lib/postgresql/data"

  redis:
    image: redis:6

volumes:
  db_data:
```

## 🛠️ Technical Stack (Notification Service)

- **Language:** Python 3.14
- **Libraries:**
  - `websockets`: For real-time streaming from the API.
  - `httpx`: For sending POST requests to `ntfy.sh`.
  - `PyYAML`: For configuration management.
- **Infrastructure:** `systemd` or `launchd` unit for background process management.

## 📡 Data Flow

1.  **Connection:** Establish a WebSocket connection to `wss://tt.horner.tj` (or `ws://localhost:3000`).
2.  **Subscription:** Send a `schedule:subscribe` event for configured `routeStopPairs`.
3.  **Monitoring:** Listen for `schedule` events.
4.  **ntfy.sh Integration:**
    - When a bus is `threshold` minutes away, send a POST request:
    ```bash
    curl -d "Bus 255 arriving in 5 mins!" ntfy.sh/your-topic
    ```
    - Use headers like `X-Title`, `X-Priority: 4`, and `X-Tags: bus,warning` for rich notifications.

## 📝 Implementation Phases

### Phase 1: Environment Setup (Complete)
- Create a virtual environment using `uv`.
- (Optional) Set up the Docker containers for the self-hosted API.

### Phase 2: Core Development (Complete)
- Write the WebSocket client in `main.py`.
- Implement `ntfy.sh` alerting logic with deduplication (cache `tripId`s in memory).

### Phase 3: Deployment (Complete)
- Configure the service to run on boot on the Mac Mini.
- Verify notifications on your mobile device via the ntfy app.

### Phase 4: Interactive TUI Configurator (Current)
- Replace the basic configurator with an advanced TUI.
- **Location & Route Selection:**
  - Allow the user to select a route within a bounding box (bbox) near the current user location.
  - Reverse geocode the location from nearest crossing streets.
- **Stop Configuration:**
  - Follow stops along the selected route to configure and add desired stops to the configuration.
- **Simulator Accuracy (Fixed):**
  - **Width:** Correctly uses `32 * num_panels` (64 chars for 2 panels).
  - **Time Display:** Uses raw minutes (`display_offset: false`) and clamps at `0m` to match hardware behavior.

### Phase 5: Self-Hosted Infrastructure & Extensible API (Future)
- **Container Service:** Self-host the container as a dedicated service on the Mac Mini.
- **Web API:** Extend the application into an extensible Python web API running on the local network, hosted on the Mac Mini.
