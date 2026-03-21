import asyncio
import os
import sys
import numpy as np
import cv2
from datetime import datetime

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from transit_tracker.config import TransitConfig
from transit_tracker.simulator import LEDSimulator

async def capture_sim_to_image():
    config = TransitConfig.load(".local/accurate_config.yaml")
    config.service.use_local_api = True
    config.api_url = "ws://localhost:8000"
    
    sim = LEDSimulator(config, force_live=True)
    task = asyncio.create_task(sim._listen_websocket())
    
    # Wait for data
    print("Waiting for simulator data...")
    for _ in range(50):
        if "live" in sim.state and sim.state["live"].get("trips"):
            break
        await asyncio.sleep(0.1)
    
    if not ("live" in sim.state and sim.state["live"].get("trips")):
        print("No live data received.")
        # Try mock if live fails
        sim = LEDSimulator(config, force_live=False)
        
    deps = sim.get_upcoming_departures()
    if not deps:
        print("No departures to render.")
        return

    # Constants
    panel_width = 64
    num_panels = 2
    display_width = panel_width * num_panels
    row_height = 8 # 7 rows + 1 spacer
    display_height = row_height * 4 # 4 rows max
    
    # Create image (black background)
    # Each 'dot' will be 4x4 pixels
    scale = 4
    img = np.zeros((display_height * scale, display_width * scale, 3), dtype=np.uint8)
    
    elapsed = 0
    for i, dep in enumerate(deps[:4]):
        # Get canvas from simulator logic
        # We need to reach into _render_trip_row but get the canvas before it's converted to Text
        # I'll re-implement the canvas logic briefly for high-fidelity image output
        
        # 1. Prepare segments
        route_text = str(dep['route'])
        headsign_text = dep['headsign']
        time_text = "Now" if dep['diff'] <= 0 else f"{dep['diff']}m"
        is_realtime = dep['live']
        
        # 2. Get bitmaps
        route_bm = sim.microfont.get_bitmap(route_text)
        route_w = len(route_bm[0])
        time_bm = sim.microfont.get_bitmap(time_text)
        time_w = len(time_bm[0])
        
        # 3. Headsign Area
        headsign_x_start = route_w + 3
        icon_w = 6 if is_realtime else 0
        headsign_area_w = display_width - headsign_x_start - time_w - (icon_w + 2 if is_realtime else 0)
        headsign_bm_full = sim.microfont.get_bitmap(headsign_text)
        headsign_full_w = len(headsign_bm_full[0])
        
        # 4. Color Mapping (BGR for OpenCV)
        colors = {
            "yellow": (0, 255, 255),
            "hot_pink": (180, 105, 255),
            "bright_blue": (255, 191, 0),
            "white": (255, 255, 255),
            "grey74": (188, 188, 188),
            "dim black": (20, 20, 20)
        }
        
        row_y_offset = i * row_height
        
        # Draw Route
        route_color = colors.get(dep['color'], colors["yellow"])
        for r in range(7):
            for c in range(route_w):
                if route_bm[r][c]:
                    cv2.rectangle(img, 
                                  (c * scale + 1, (row_y_offset + r) * scale + 1),
                                  ((c + 1) * scale - 1, (row_y_offset + r + 1) * scale - 1),
                                  route_color, -1)
        
        # Draw Time
        time_x = display_width - time_w
        time_color = colors["bright_blue"] if is_realtime else colors["grey74"]
        for r in range(7):
            for c in range(time_w):
                if time_bm[r][c]:
                    tx = time_x + c
                    cv2.rectangle(img,
                                  (tx * scale + 1, (row_y_offset + r) * scale + 1),
                                  ((tx + 1) * scale - 1, (row_y_offset + r + 1) * scale - 1),
                                  time_color, -1)
                                  
        # Draw Icon
        if is_realtime:
            icon_bm = sim.microfont.get_live_icon_frame(0) # Static for snap
            icon_x = time_x - 8
            for r in range(6):
                for c in range(6):
                    val = icon_bm[r][c]
                    if val:
                        color = colors["white"] if val == 2 else colors["bright_blue"]
                        ix = icon_x + c
                        cv2.rectangle(img,
                                      (ix * scale + 1, (row_y_offset + r) * scale + 1),
                                      ((ix + 1) * scale - 1, (row_y_offset + r + 1) * scale - 1),
                                      color, -1)
                                      
        # Draw Headsign
        for r in range(7):
            for c in range(min(headsign_area_w, headsign_full_w)):
                if headsign_bm_full[r][c]:
                    hx = headsign_x_start + c
                    cv2.rectangle(img,
                                  (hx * scale + 1, (row_y_offset + r) * scale + 1),
                                  ((hx + 1) * scale - 1, (row_y_offset + r + 1) * scale - 1),
                                  colors["white"], -1)

    output_path = "sim_rendered.png"
    cv2.imwrite(output_path, img)
    print(f"Success! Rendered simulator to {output_path}")

    sim.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    asyncio.run(capture_sim_to_image())
