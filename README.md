# 🏙️ Transit Tracker

A lightweight, terminal-based transit data proxy for macOS. It monitors public transit arrivals using the OneBusAway API and GTFS-Realtime feeds, providing an exact 1-to-1 WebSocket API compatible with the official Transit Tracker LED matrix hardware.

## ✨ Features

- **Interactive Configurator:** A beautiful, inline Terminal User Interface built with `rich` and `questionary`.
- **Location-based Routing:** Search for cross-streets (e.g., "Rainier Blvd & Charles St, Issaquah"), automatically reverse-geocode them via OpenStreetMap Nominatim, and find nearby transit routes.
- **Background Daemon:** Runs silently in the background on your Mac using `launchd`.
- **Reference Compatibility:** Provides the EXACT WebSocket payload expected by the reference ESP32 firmware.

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
*The hardware connects to this Python service running on your local network, which proxies the data.*

```mermaid
sequenceDiagram
    participant HW as LED Matrix (ESP32)
    participant Py as transit-tracker (Local Python)
    participant API as Transit API (OneBusAway)

    HW->>Py: WebSocket Connection (websocket_server.py)
    Py->>API: Fetch Arrival Data (transit_api.py)
    API-->>Py: Raw Transit Data
    Py-->>HW: Proxy Updates to Display
    Note over Py: logic in websocket_service.py
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

- **Waveshare RGB Full-Color LED Matrix Panel (64×32 Pixels):** 2.5mm pitch.
- **Adafruit ESP32-S3 LED Matrix Portal:** A specialized driver board for HUB75 panels.
- **Related Hardware:** The system is built around the ESP32-S3 architecture and standard HUB75 64x32 RGB panels.

### Initial Firmware (Unboxing) & Upgrades

If you are unboxing a brand new ESP32 board, it must be flashed with the base transit-tracker ESPHome firmware. 
- **From this TUI:** When you click "Flash Device", the application will automatically detect if your board lacks the base firmware. If so, it will prompt you and securely download the latest official `firmware.factory.bin` directly from the [EastsideUrbanism/transit-tracker releases page](https://github.com/EastsideUrbanism/transit-tracker/releases) and flash it over USB.

### Upstream Sources

To stay up to date with the core project:
- **Firmware Binary Updates:** Releases are published to [EastsideUrbanism/transit-tracker/releases](https://github.com/EastsideUrbanism/transit-tracker/releases). You can apply OTA updates via the web configurator or ESPHome dashboard.
- **Data APIs:** The transit data proxy is hosted at `wss://tt.horner.tj`. The underlying API project container is maintained at [tjhorner/transit-tracker-api](https://github.com/tjhorner/transit-tracker-api). You can self-host the API using the official Docker image (`ghcr.io/tjhorner/transit-tracker-api:latest`).

## 📸 Hardware Capture & Auto-Crop

The project includes a specialized utility for validating your physical LED board against the simulator. Because LED matrices use PWM (multiplexing), they often appear jittery or have black bands in single photos. 

This tool uses **Temporal Frame Averaging** to merge multiple frames from a video/timelapse into a single smooth image, then uses **Color-Selective Template Matching** to automatically identify and crop the transit board from the photo using the simulator as a "fingerprint".

### 1. Capture & Average
If you have a DJI Action 4 or similar camera connected, you can use the background watcher to automatically process new timelapse footage:

```bash
uv run python scripts/watch_and_average.py
```

### 2. Auto-Crop the Board
To reliably isolate the transit board from a photo (even if it's handheld or has background clutter):

```bash
uv run transit-tracker-capture [path_to_image.jpg]
```

**How it works:**
- It generates a high-fidelity "template" from the live simulator.
- It masks for the specific **Hot Pink (Route 14)** and **Neon Yellow** LED colors.
- It performs a multi-scale search to find that exact pixel pattern in your photo.
- It saves the result to `captures/capture_YYYYMMDD_HHMMSS.jpg`.

## 🛠️ Development

If you are developing or modifying the codebase, you can run tests using:

```bash
uv run pytest
```
