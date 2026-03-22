import asyncio
import os
import sys

try:
    import cv2
    import numpy as np
except ImportError:
    raise ImportError(
        "capture requires opencv and numpy. "
        "Install with: uv pip install transit-tracker[capture]"
    )
from datetime import datetime
from typing import Tuple, Optional

from .config import TransitConfig
from .simulator import LEDSimulator

def get_pink_mask(img):
    """Isolates the neon/hot pink pixels common in Route 14."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # Hot Pink/Magenta range
    lower_pink = np.array([140, 50, 50])
    upper_pink = np.array([175, 255, 255])
    return cv2.inRange(hsv, lower_pink, upper_pink)

async def generate_sim_template(config: TransitConfig) -> np.ndarray:
    """Generates a high-fidelity image of the simulator state to use as a template."""
    sim = LEDSimulator(config, force_live=True)
    
    # Try to get live data, fallback to mock if needed
    task = asyncio.create_task(sim._listen_websocket())
    for _ in range(30):
        if "live" in sim.state and sim.state["live"].get("trips"):
            break
        await asyncio.sleep(0.1)
    
    deps = sim.get_upcoming_departures()
    if not deps:
        sim = LEDSimulator(config, force_live=False)
        deps = sim.get_upcoming_departures()

    # Constants matching render_sim.py
    scale = 4
    display_width = config.service.panel_width * config.service.num_panels
    img = np.zeros((32 * scale, display_width * scale, 3), dtype=np.uint8)
    
    for i, dep in enumerate(deps[:4]):
        row_y = i * 8
        if row_y >= 32: break
        
        # Simple render logic for template
        route_bm = sim.microfont.get_bitmap(str(dep['route']))
        for r in range(7):
            for c in range(len(route_bm[0])):
                if route_bm[r][c]:
                    color = (180, 105, 255) if dep['color'] == "hot_pink" else (0, 255, 255)
                    cv2.rectangle(img, (c*scale, (row_y+r)*scale), ((c+1)*scale, (row_y+r+1)*scale), color, -1)

    sim.running = False
    task.cancel()
    return img

def find_and_crop(image_path: str, sim_template: np.ndarray) -> Optional[np.ndarray]:
    """Uses the simulator template to find and crop the board from a photo."""
    img = cv2.imread(image_path)
    if img is None: return None
    
    img_h, img_w = img.shape[:2]
    img_center = (img_w // 2, img_h // 2)

    img_mask = get_pink_mask(img)
    sim_mask = get_pink_mask(sim_template)
    
    found = None
    # Optimized scale search
    for scale in np.linspace(0.4, 4.0, 50):
        resized_sim_mask = cv2.resize(sim_mask, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        tH, tW = resized_sim_mask.shape[:2]
        if tH > img_h or tW > img_w: continue
            
        res = cv2.matchTemplate(img_mask, resized_sim_mask, cv2.TM_CCOEFF_NORMED)
        
        # Center-weighting
        res_h, res_w = res.shape
        y_indices, x_indices = np.indices((res_h, res_w))
        dist = np.sqrt(((x_indices + tW//2 - img_center[0])/img_w)**2 + 
                       ((y_indices + tH//2 - img_center[1])/img_h)**2)
        res_weighted = res * (1.0 - 0.7 * dist)
        
        (_, maxVal, _, maxLoc) = cv2.minMaxLoc(res_weighted)
        if found is None or maxVal > found[0]:
            found = (maxVal, maxLoc, scale, tW, tH)

    if found and found[0] > 0.05:
        _, (x, y), _, tW, tH = found
        pad_x, pad_y = int(tW * 0.2), int(tH * 0.2)
        return img[max(0, y-pad_y):min(img_h, y+tH+pad_y), 
                   max(0, x-pad_x):min(img_w, x+tW+pad_x)]
    return None

import subprocess

async def run_capture(input_path: str = "hardware_averaged.jpg"):
    os.makedirs("captures", exist_ok=True)
    from .config import get_last_config_path
    path = get_last_config_path() or ".local/accurate_config.yaml"
    config = TransitConfig.load(path)
    
    print(f"Generating template from simulator...")
    template = await generate_sim_template(config)
    
    print(f"Searching for board in {input_path}...")
    cropped = find_and_crop(input_path, template)
    
    if cropped is not None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = f"captures/capture_{timestamp}.jpg"
        cv2.imwrite(output_path, cropped)
        # Also update the 'latest' for convenience
        cv2.imwrite("hardware_cropped.jpg", cropped)
        print(f"Successfully captured board to {output_path}")
        subprocess.run(["open", output_path])
    else:
        print("Failed to identify transit board in photo.")

def main():
    import subprocess
    input_file = sys.argv[1] if len(sys.argv) > 1 else "hardware_averaged.jpg"
    asyncio.run(run_capture(input_file))

if __name__ == "__main__":
    main()
