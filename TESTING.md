# 🧪 Testing Strategy & Analysis

This document provides a comprehensive overview of the testing strategy for the **Transit Tracker** Python service. Our goal is to ensure 100% functional parity and protocol compatibility with the [tjhorner/transit-tracker-api](https://github.com/tjhorner/transit-tracker-api) reference project, while providing a robust local environment for macOS users.

## 📐 The Testing Pyramid

We employ a multi-layered testing strategy, moving from isolated logic to full-system simulations.

### 1. Unit & Core Logic (Base)
*   **Files:** `test_config.py`, `test_cli.py`, `test_tui_wizards.py`
*   **Purpose:** Validates individual components like configuration parsing, TUI state transitions, and CLI argument handling.
*   **Errors Caught:**
    *   `ValidationError`: Pydantic catching incorrect types in `config.yaml`.
    *   `UnboundLocalError`: CLI failing when optional arguments are missing.
    *   `StateMismatch`: TUI wizards not correctly updating the internal `TransitConfig`.

### 2. Integration & Network (Middle)
*   **Files:** `test_network.py`, `test_service_isolation.py`, `test_protocol_comparison.py`, `test_proxy_equivalence.py`
*   **Purpose:** Ensures the WebSocket server and client can communicate using the EXACT schema required by the hardware.
*   **Errors Caught:**
    *   `KeyError: 'payload'`: Detecting when the local proxy sends a `data` key instead of the reference-required `payload` key.
    *   `ProtocolMismatch`: Catching missing top-level `stopId` fields that would cause the ESP32 firmware to ignore updates.
    *   `Filtering Error`: Identifying when past or stale trips are not correctly pruned from the live broadcast.

### 3. System & Simulator (Top)
*   **Files:** `test_captures.py`, `test_simulator_mock_equivalence.py`, `test_firmware_correctness.py`
*   **Purpose:** High-fidelity end-to-end simulation. This layer ensures that the final rendered text on the LED matrix (simulated) perfectly matches expected behavior.
*   **Errors Caught:**
    *   `Now Bug`: Catching massive negative minute offsets (e.g., "-2938423m") when `departureTime` is missing.
    *   `Offset Inconsistency`: Verifying that a `-2min` offset applied by the server results in exactly `8m` being displayed for a `10m` arrival.
    *   `Visual Discrepancy`: Using hardware captures in `accurate_config.yaml` to detect when the simulator's scrolling speed or truncation logic differs from the physical panel.

---

## 🔗 Reference Compatibility Verification

To ensure this Python service is a drop-in replacement for the official Node.js container, we use a specialized "Contract Test" pattern:

1.  **Schema Enforcement:** `test_protocol_comparison.py` runs a "side-by-side" validator. It generates a mock trip, processes it through our `TransitServer`, and asserts that every field (including nesting in `payload`) matches the reference implementation.
2.  **Dumb Firmware Model:** `test_firmware_correctness.py` includes a `test_dumb_firmware_compatibility` case. This specifically models the C++ logic used in the ESP32 firmware:
    ```cpp
    // The firmware is 'dumb' and only does:
    int display_mins = (json["arrivalTime"] - sntp_now) / 60;
    ```
    Our tests ensure that our server-side "spoofing" of the `arrivalTime` results in the correct `display_mins` on a device with zero local logic.

## 🛡️ Local Guardrails

We enforce a "Green Build" policy through two primary mechanisms:

1.  **`scripts/ci_local.sh`**: A shell script that mirrors our GitHub Actions (GHA) workflow. It runs `pytest`, verifies CLI launch, and builds the package.
2.  **Git Pre-Push Hook**: Every `git push` automatically triggers `ci_local.sh`. If any test fails, the push is aborted, preventing broken code from ever reaching the repository.

---

## 🏃 Running Tests

To run the full suite locally:
```bash
./scripts/ci_local.sh
```

To run a specific layer (e.g., only Simulator tests):
```bash
uv run pytest tests/test_simulator_mock_equivalence.py
```
