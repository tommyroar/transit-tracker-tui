import os
import sys
import yaml
import pytest

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
    
    # Generate mock state from the text
    mock_buses = []
    for line in display_text.split('\n'):
        parts = line.split()
        if not parts: continue
        route = parts[0]
        live = "{LIVE}" in line
        time_str = parts[-1].replace("{LIVE}", "").replace("m", "")
        diff = 0 if time_str == "Now" else int(time_str)
        
        line_clean = line.replace("{LIVE}", " ").strip()
        parts_clean = line_clean.split()
        headsign = " ".join(parts_clean[1:-1])
        
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
    
    assert actual_text == display_text

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
