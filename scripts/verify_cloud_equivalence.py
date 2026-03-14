import asyncio
import json
import websockets
import time
import sys
from typing import Dict, Any

CLOUD_URL = "wss://tt.horner.tj/"
LOCAL_URL = "ws://localhost:8000"

# Standard test pairs
HANDSHAKE_PAIRS = "40_100240,1_8494,-420;1_100039,1_11920,-540"

async def get_update(url: str, name: str):
    """Connects to a proxy and waits for the first schedule update."""
    print(f"[{name}] Connecting to {url}...")
    try:
        async with asyncio.timeout(120): # Long timeout for cloud/rate-limits
            async with websockets.connect(url) as ws:
                # 1. Send handshake
                handshake = {
                    "event": "schedule:subscribe",
                    "data": {
                        "routeStopPairs": HANDSHAKE_PAIRS,
                        "limit": 5
                    }
                }
                await ws.send(json.dumps(handshake))
                
                # 2. Wait for schedule update
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if data.get("event") == "schedule":
                        return data["data"]
    except Exception as e:
        print(f"[{name}] Error: {e}")
        return None

async def main():
    """Runs a live comparison between the local proxy and the cloud baseline."""
    print("🏙️  Transit Tracker Equivalence Verifier")
    print("Comparing local proxy against cloud baseline...\n")

    local_data = await get_update(LOCAL_URL, "LOCAL")
    cloud_data = await get_update(CLOUD_URL, "CLOUD")
    
    if not local_data:
        print("\n❌ FAILED: Could not retrieve data from LOCAL proxy.")
        print("   Ensure 'transit-tracker service' is running locally.")
        sys.exit(1)
        
    if not cloud_data:
        print("\n⚠️  WARNING: Could not retrieve data from CLOUD proxy.")
        print("   This is often due to cloud-side rate limiting or connection issues.")
        print("   Local data is still provided below for manual verification.")

    print("\n" + "="*60)
    print(f"{'ROUTE':<10} | {'HEADSIGN':<25} | {'DUE':<5} | {'LIVE':<5} | {'ID'}")
    print("-" * 60)
    
    def format_trip(t):
        arr = t.get("arrivalTime", 0)
        now = int(time.time())
        diff = (arr - now) // 60
        is_live = "✅" if t.get("isRealtime") else "❌"
        return f"{t.get('routeName'):<10} | {t.get('headsign'):<25} | {diff:>3}m | {is_live:<5} | {t.get('tripId')}"

    print(f"LOCAL PROXY ({len(local_data.get('trips', []))} trips):")
    for t in local_data.get("trips", []):
        print(f"  {format_trip(t)}")
            
    if cloud_data:
        print(f"\nCLOUD PROXY ({len(cloud_data.get('trips', []))} trips):")
        for t in cloud_data.get("trips", []):
            print(f"  {format_trip(t)}")
            
        # Structural check
        print("\n" + "="*60)
        print("STRUCTURAL VALIDATION")
        
        l_keys = set(local_data.keys())
        c_keys = set(cloud_data.keys())
        
        if l_keys == c_keys:
            print("✅ Top-level schema matches cloud baseline")
        else:
            print(f"❌ Schema mismatch! Local={l_keys}, Cloud={c_keys}")
            
        if local_data.get("trips") and cloud_data.get("trips"):
            l_trip = local_data["trips"][0]
            c_trip = cloud_data["trips"][0]
            if set(l_trip.keys()) == set(c_trip.keys()):
                print("✅ Trip object protocol matches exactly")
            else:
                print("❌ Trip protocol divergence detected!")
                print(f"   Missing in Local: {set(c_trip.keys()) - set(l_trip.keys())}")
                print(f"   Extra in Local:   {set(l_trip.keys()) - set(c_trip.keys())}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
