import os
import sys
import yaml
import pytest
from datetime import datetime
from typing import List, Dict, Any

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from transit_tracker.config import TransitConfig
from transit_tracker.simulator import LEDSimulator

def parse_capture_line(line: str) -> Dict[str, Any]:
    """Parses a line like '14 Downtown Seattle {LIVE}11m' into a bus dict."""
    parts = line.split()
    route = parts[0]
    live = "{LIVE}" in line
    # Time is at the end, remove '{LIVE}' and 'm'
    time_str = parts[-1].replace("{LIVE}", "").replace("m", "")
    try:
        diff = int(time_str)
    except ValueError:
        # Handle 'Now' or other non-int if they appear
        diff = 0
        
    # Headsign is everything in between
    # Find start of headsign (after route) and end (before {LIVE} or time)
    line_clean = line.replace("{LIVE}", " ").strip()
    parts_clean = line_clean.split()
    headsign = " ".join(parts_clean[1:-1])
    
    return {
        "route": route,
        "headsign": headsign,
        "diff": diff,
        "live": live,
        "color": "cyan" if route == "14" else "yellow"
    }

def get_captures():
    config_path = os.path.join(os.path.dirname(__file__), "..", "accurate_config.yaml")
    if not os.path.exists(config_path):
        return []
    with open(config_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("captures", [])

@pytest.mark.parametrize("capture", get_captures())
def test_simulator_output_matches_capture(capture):
    """
    Validates that the simulator's rendering logic produces strings that match 
     the user's captured display output.
    """
    display_lines = capture["display"].strip().split('\n')
    expected_buses = [parse_capture_line(l) for l in display_lines]
    
    # Setup config with mock state
    config = TransitConfig()
    config.num_panels = 2
    config.mock_state = expected_buses
    
    sim = LEDSimulator(config)
    
    # We mock _render_led_string to capture the actual strings being sent to the "LEDs"
    actual_rendered_strings = []
    
    def mock_render(text, color="yellow"):
        actual_rendered_strings.append(text)
        # Return a dummy Text object to keep the original logic happy
        from rich.text import Text
        return Text(text)
    
    sim._render_led_string = mock_render
    sim._generate_frame()
    
    # Validation
    assert len(actual_rendered_strings) == len(display_lines), "Number of rendered lines mismatch"
    
    for i, actual in enumerate(actual_rendered_strings):
        expected_raw = display_lines[i]
        bus = expected_buses[i]
        
        # Reconstruction of the expected string based on simulator logic
        icon = "*" if bus["live"] else " "
        eta_part = f"{icon}{bus['diff']}m"
        r_str = f"{str(bus['route'])[:3]:>3}"
        
        # char_width for 2 panels is 32
        char_width = 32
        fixed_len = 3 + 1 + 1 + len(eta_part)
        max_h = char_width - fixed_len
        h_text = bus['headsign'][:max_h]
        
        expected_rendered = f"{r_str} {h_text:<{max_h}} {eta_part}"
        
        print(f"\nLine {i+1}:")
        print(f"  Actual:   '{actual}'")
        print(f"  Expected: '{expected_rendered}'")
        
        assert len(actual) == 32, f"Line {i+1} width is not 32"
        assert actual == expected_rendered, f"Line {i+1} content mismatch"

if __name__ == "__main__":
    # Allow running directly
    for cap in get_captures():
        try:
            test_simulator_output_matches_capture(cap)
            print(f"PASS: {cap['time']}")
        except AssertionError as e:
            print(f"FAIL: {cap['time']} - {e}")
