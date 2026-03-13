import asyncio
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))
from transit_tracker.config import TransitConfig
from transit_tracker.simulator import LEDSimulator

async def main():
    config = TransitConfig.load(".local/accurate_config.yaml")
    config.use_local_api = True
    config.api_url = "ws://localhost:8000"
    
    sim = LEDSimulator(config, force_live=True)
    task = asyncio.create_task(sim._listen_websocket())
    
    for _ in range(50):
        if "live" in sim.state and sim.state["live"].get("trips"):
            break
        await asyncio.sleep(0.1)
        
    print(sim.get_current_display_text())
    
    sim.running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    asyncio.run(main())
