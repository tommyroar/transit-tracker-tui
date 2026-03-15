import os
import sys

import pytest
import yaml

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from transit_tracker.config import TransitConfig
from transit_tracker.simulator import LEDSimulator


def get_captures():
    candidates = [
        os.path.join(os.path.dirname(__file__), "..", "accurate_config.yaml"),
        os.path.join(os.path.dirname(__file__), "..", ".local", "accurate_config.yaml")
    ]
    config_path = next((c for c in candidates if os.path.exists(c)), None)
    if not config_path: return []
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("captures", [])

@pytest.mark.parametrize("capture", get_captures())
def test_capture_match(capture):
    display_text = capture["display"].strip()
    
    # Generate mock state from the text.
    # Lines follow the display template: "{ROUTE}  {HEADSIGN}  {LIVE} {TIME}"
    # Legacy captures may use single-space separators; handle both.
    mock_buses = []
    for line in display_text.split('\n'):
        line = line.strip()
        if not line:
            continue
        live = "{LIVE}" in line
        line_clean = line.replace("{LIVE}", "").strip()
        parts = line_clean.split()
        if not parts:
            continue
        route = parts[0]
        time_str = parts[-1].replace("m", "")
        diff = 0 if time_str == "Now" else int(time_str)
        headsign = " ".join(parts[1:-1])

        mock_buses.append({
            "route": route,
            "headsign": headsign,
            "diff": diff,
            "live": live,
            "color": "pink" if "14" in route else "yellow"
        })
        
    config = TransitConfig()
    config.num_panels = 2
    config.mock_state = mock_buses
    
    sim = LEDSimulator(config, force_live=False)
    actual_text = sim.get_current_display_text()

    # Compare semantic content (route, time) rather than exact formatting,
    # since the display template may change spacing/order.
    def parse_lines(text):
        result = []
        for ln in text.strip().split('\n'):
            ln = ln.strip()
            if not ln:
                continue
            has_live = "{LIVE}" in ln
            ln_clean = ln.replace("{LIVE}", "").strip()
            p = ln_clean.split()
            if not p:
                continue
            result.append((p[0], p[-1], has_live))
        return result

    assert parse_lines(actual_text) == parse_lines(display_text)

if __name__ == "__main__":
    caps = get_captures()
    passed = 0
    for cap in caps:
        try:
            test_capture_match(cap)
            passed += 1
        except Exception as e:
            print(f"FAIL: {cap['time']} - {e}")
    print(f"\nSummary: {passed}/{len(caps)} Captures Passed")
    if passed < len(caps): sys.exit(1)
