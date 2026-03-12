# 🏙️ Transit Tracker

A lightweight, terminal-based transit notification service for macOS. It monitors public transit arrivals using the OneBusAway API and GTFS-Realtime feeds, sending push notifications to your devices via [ntfy.sh](https://ntfy.sh/) when your bus or train is approaching.

## ✨ Features

- **Interactive Configurator:** A beautiful, inline Terminal User Interface built with `rich` and `questionary`.
- **Location-based Routing:** Search for cross-streets (e.g., "Rainier Blvd & Charles St, Issaquah"), automatically reverse-geocode them via OpenStreetMap Nominatim, and find nearby transit routes.
- **Background Daemon:** Runs silently in the background on your Mac using `launchd`.
- **Push Notifications:** Pushes rich alerts to your phone via `ntfy.sh` (e.g., "Bus 554 arriving in 5 mins!").

## 🏗️ Architecture

The project supports two primary modes of operation: connecting to a cloud-based proxy or hosting a local WebSocket server that interacts directly with transit APIs.

### 1. Default Configuration (Cloud)
*The hardware connects directly to the public WebSocket API hosted by TJ Horner.*

```mermaid
sequenceDiagram
    participant HW as LED Matrix (ESP32)
    participant Cloud as tt.horner.tj (Remote)
    participant API as Transit API (OneBusAway)

    HW->>Cloud: WebSocket Connection (Subscribe)
    Cloud->>API: Poll Arrival Data
    API-->>Cloud: XML/JSON Response
    Cloud-->>HW: Push Real-time Updates (JSON)
```

### 2. Local WebSocket Host
*The hardware connects to this Python service running on your local network, which proxies the data and manages local notifications.*

```mermaid
sequenceDiagram
    participant HW as LED Matrix (ESP32)
    participant Py as transit-tracker (Local Python)
    participant API as Transit API (OneBusAway)
    participant NTFY as ntfy.sh (Push Service)

    HW->>Py: WebSocket Connection (websocket_server.py)
    Py->>API: Fetch Arrival Data (transit_api.py)
    API-->>Py: Raw Transit Data
    Py-->>HW: Proxy Updates to Display
    Note over Py: logic in websocket_service.py
    Py->>NTFY: Trigger Push Notifications
```

## 📦 Installation

This project is built and managed using `uv`. To install it globally as a self-contained command-line tool, run the following from the project directory:

```bash
uv tool install .
```

This creates an isolated virtual environment and links the `transit-tracker` executable to your system path.

## 🚀 Usage

Once installed, you can run the tool from anywhere in your terminal.

### 1. Launch the TUI (Configurator)

To open the interactive dashboard:

```bash
transit-tracker
# or
transit-tracker ui
```

**Inside the TUI:**
1. Click **Add Stop**.
2. Type in an intersection or address (e.g., `Rainier Blvd & Charles St, Issaquah`).
3. Select a nearby route.
4. Select the specific stop and direction.
5. Click **Save Changes**.

### 2. Start the Background Service

You can start the background monitor directly from the TUI (using the "Start Service" button), which automatically creates and registers a macOS `launchd` plist file so it runs continuously.

Alternatively, you can run the service directly in the foreground for debugging:

```bash
transit-tracker service
```

## ⚙️ Configuration

Configuration is saved in a local `config.yaml` file in your current working directory when saving from the TUI.

```yaml
api_url: wss://tt.horner.tj
ntfy_topic: transit-alerts
arrival_threshold_minutes: 5
check_interval_seconds: 30
subscriptions:
  - feed: st
    route: 1_100236
    stop: 1_80485
    label: 554 - Rainier Blvd S & E Sunset Way
```

## 🛠️ Hardware Components

This project is designed to run on specific LED matrix hardware. Below are the components used in this build:

- **Waveshare RGB Full-Color LED Matrix Panel (64×32 Pixels):** 2.5mm pitch. Ordered on March 2, 2026 (Order # 113-9435943-2623440), and delivered on March 2, 2026.
- **Adafruit ESP32-S3 LED Matrix Portal:** A specialized driver board for HUB75 panels. (Adafruit Order #3641312, shipped March 3, 2026). [Track Delivery](https://tools.usps.com/go/TrackConfirmAction?qtc_tLabels1=9405540106246002702780).
- **Related Hardware:** The system is built around the ESP32-S3 architecture and standard HUB75 64x32 RGB panels.

### Initial Firmware (Unboxing) & Upgrades

If you are unboxing a brand new ESP32 board, it must be flashed with the base transit-tracker ESPHome firmware. 
- **From this TUI:** When you click "Flash Device", the application will automatically detect if your board lacks the base firmware. If so, it will prompt you and securely download the latest official `firmware.factory.bin` directly from the [EastsideUrbanism/transit-tracker releases page](https://github.com/EastsideUrbanism/transit-tracker/releases) and flash it over USB.
- **From the Web:** You can also perform this initial flash from the official web configurator at [config.transit-tracker.eastsideurbanism.org](https://config.transit-tracker.eastsideurbanism.org).

### Upstream Sources

To stay up to date with the core project:
- **Firmware Binary Updates:** Releases are published to [EastsideUrbanism/transit-tracker/releases](https://github.com/EastsideUrbanism/transit-tracker/releases). You can apply OTA updates via the web configurator or ESPHome dashboard.
- **Data APIs:** The transit data proxy is hosted at `wss://tt.horner.tj`. The underlying API project container is maintained at [tjhorner/transit-tracker-api](https://github.com/tjhorner/transit-tracker-api). You can self-host the API using the official Docker image (`ghcr.io/tjhorner/transit-tracker-api:latest`).

## 🛠️ Development

If you are developing or modifying the codebase, you can run tests using:

```bash
uv run pytest
```
