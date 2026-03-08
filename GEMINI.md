# 🏙️ Transit Notification Service - Gemini Project Context

## 🧪 Simulator Testing & Debugging Loop (MANDATORY)

To ensure the simulator perfectly matches the physical hardware display, follow this iterative process:

1.  **Record Input Data:** In `accurate_config.yaml`, add a new entry to `captures`:
    - `time`: Timestamp of the test.
    - `display`: EXACT text observed on the **real hardware display**.
    - `simulator`: EXACT text observed on the **live simulator** (before the fix).
2.  **Reproduction:** Run `uv run pytest tests/test_captures.py` from the `transit-tracker-tui` directory to confirm the failure.
3.  **Debug & Fix:** Identify discrepancies in `simulator.py` logic (e.g., time offsets, route name parsing, truncation, scroll speed) and apply fixes.
4.  **Verify:** Re-run the test harness until all captures pass.
5.  **Loop:** Repeat until the simulator's rendered output is char-for-char identical to the hardware display.

A task involving the simulator is **not complete** until all recorded captures in `accurate_config.yaml` pass verification.

## 📁 Directory Structure & Files

- **`accurate_config.yaml`**: Contains current live configuration and reference captures.
- **`src/transit_tracker/tui.py`**: Main TUI logic (uses `rawselect` for number-based selection).
- **`src/transit_tracker/simulator.py`**: LED Simulator logic.
- **`tests/test_captures.py`**: Regression test suite for capture verification.
