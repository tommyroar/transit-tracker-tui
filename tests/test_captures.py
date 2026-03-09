import os
import sys
import yaml
import pytest
from datetime import datetime, timezone
from typing import List, Dict, Any

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from transit_tracker.config import TransitConfig
from transit_tracker.simulator import LEDSimulator

def parse_capture_line(line: str) -> Dict[str, Any]:
    """Parses a line like '14 Downtown Seattle {LIVE}11m' into a bus dict."""
    parts = line.split()
    if not parts: return None
    route = parts[0]
    live = "{LIVE}" in line
    # Time is at the end, remove '{LIVE}' and 'm'
    time_str = parts[-1].replace("{LIVE}", "").replace("m", "")
    try:
        diff = int(time_str)
    except ValueError:
        diff = 0
        
    line_clean = line.replace("{LIVE}", " ").strip()
    parts_clean = line_clean.split()
    if len(parts_clean) < 2: return None
    headsign = " ".join(parts_clean[1:-1])
    
    return {
        "route": route,
        "headsign": headsign,
        "diff": diff,
        "live": live,
        "color": "pink" if route == "14" else "yellow"
    }

def get_captures():
    config_path = os.path.join(os.path.dirname(__file__), "..", "accurate_config.yaml")
    if not os.path.exists(config_path):
        return []
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("captures", [])

@pytest.mark.parametrize("capture", get_captures())
def test_capture_match(capture):
    """
    Validates that the simulator generates the exact LED strings from the capture data.
    """
    display_text = capture["display"].strip()
    expected_lines = display_text.split('\n')[:3]
    
    # Setup mock buses from the capture string
    mock_buses = []
    for line in expected_lines:
        bus = parse_capture_line(line)
        if bus: mock_buses.append(bus)
    
    # Setup simulator with this mock state
    config = TransitConfig()
    config.num_panels = 2
    config.mock_state = mock_buses
    
    sim = LEDSimulator(config, force_live=False)
    
    # Capture rendered strings
    actual_rendered = []
    def mock_render(text_or_spans, color="yellow"):
        if isinstance(text_or_spans, list):
            text = "".join(t for t, c in text_or_spans)
        else:
            text = text_or_spans
        actual_rendered.append(text)
        from rich.text import Text
        return Text(text)
    
    sim._render_led_string = mock_render
    sim._generate_frame()
    
    # Verification
    assert len(actual_rendered) <= 3, "Too many lines rendered"
    assert len(actual_rendered) == len(mock_buses), "Number of rendered lines mismatch"
    
    for i, actual in enumerate(actual_rendered):
        bus = mock_buses[i]
        icon = "*" if bus["live"] else " "
        eta_part = f"{icon}{bus['diff']}m"
        r_str = f"{str(bus['route'])[:3]:>3}"
        
        # Calculate padding for full width (16 chars per 64px panel)
        total_width = 16 * config.num_panels
        fixed_len = 3 + 1 + 1 + len(eta_part)
        max_h = total_width - fixed_len
        h_text = bus['headsign'][:max_h]
        
        expected_rendered = f"{r_str} {h_text:<{max_h}} {eta_part}"
        
        assert len(actual) == total_width
        assert actual == expected_rendered

if __name__ == "__main__":
    # Test runner
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
