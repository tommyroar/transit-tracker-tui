# 🏙️ Transit Notification Service - Gemini Project Context

## 🧪 Simulator Testing & Debugging Loop (MANDATORY)

To ensure the simulator perfectly matches the physical hardware display, follow this iterative process:

1.  **Capture Visual Evidence:** Run `python scripts/capture_cam.py` to take a snapshot of the **real hardware display**.
2.  **Record Input Data:** In `.local/accurate_config.yaml`, add a new entry to `captures` based on the webcam image:
    - `time`: Timestamp of the test.
    - `display`: EXACT text observed on the **real hardware display**.
    - `simulator`: EXACT text observed on the **live simulator** (before the fix).
3.  **Reproduction:** Run `uv run pytest tests/test_captures.py` from the project root to confirm the failure.
4.  **Debug & Fix:** Identify discrepancies in `src/transit_tracker/simulator.py` logic (e.g., time offsets, route name parsing, truncation, scroll speed) and apply fixes.
5.  **Verify:** Re-run the test harness until all captures pass.
6.  **Loop:** Repeat until the simulator's rendered output is char-for-char identical to the hardware display.

A task involving the simulator is **not complete** until all recorded captures in `.local/accurate_config.yaml` pass verification.

## 📁 Directory Structure & Files

- **`.local/`**: Ignored by git, contains local development configurations.
  - **`accurate_config.yaml`**: Contains current live configuration and reference captures.
  - **`config.yaml`**: Standard configuration file.
- **`src/transit_tracker/tui.py`**: Main TUI logic.
- **`src/transit_tracker/simulator.py`**: LED Simulator logic.
- **`tests/test_captures.py`**: Regression test suite for capture verification.
