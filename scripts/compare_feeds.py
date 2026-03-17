#!/usr/bin/env python3
"""30-minute comparison: local proxy vs cloud for bus and ferry feeds.

Samples both endpoints every 60s, logs trip data per feed, and prints
a summary table at the end highlighting divergences.

Usage:
    uv run python scripts/compare_feeds.py [--duration 1800]
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import websockets

LOCAL_URL = "ws://localhost:8000"
CLOUD_URL = "wss://tt.horner.tj/"

# Bus-only subscription (Sound Transit 554 + Metro 14)
BUS_PAIRS = "40_100240,1_8494,-420;1_100039,1_11920,-540"

# Ferry-only subscription (Seattle-Bainbridge)
FERRY_PAIRS = "95_73,95_7,0;95_73,95_3,0"

DURATION = int(sys.argv[sys.argv.index("--duration") + 1]) if "--duration" in sys.argv else 1800
INTERVAL = 60  # seconds between samples


async def fetch_trips(url: str, pairs: str, timeout: float = 30) -> Optional[List[Dict[str, Any]]]:
    """Connect, subscribe, wait for first schedule push, disconnect."""
    try:
        async with asyncio.timeout(timeout):
            async with websockets.connect(url) as ws:
                await ws.send(json.dumps({
                    "event": "schedule:subscribe",
                    "data": {"routeStopPairs": pairs, "limit": 10},
                }))
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if data.get("event") == "schedule":
                        return data["data"].get("trips", [])
    except Exception as e:
        return None


def fmt_trip(t: dict, now: int) -> str:
    diff = (t["arrivalTime"] - now) // 60
    rt = "RT" if t.get("isRealtime") else "SC"
    return f"{t['routeName']:<8} {t['headsign']:<28} {diff:>3}m {rt}"


async def sample_once(feed_name: str, pairs: str, now: int) -> dict:
    """Fetch from both endpoints for one feed."""
    local_task = asyncio.create_task(fetch_trips(LOCAL_URL, pairs))
    cloud_task = asyncio.create_task(fetch_trips(CLOUD_URL, pairs, timeout=45))

    local_trips = await local_task
    cloud_trips = await cloud_task

    return {
        "time": datetime.now().strftime("%H:%M:%S"),
        "feed": feed_name,
        "local_count": len(local_trips) if local_trips is not None else -1,
        "cloud_count": len(cloud_trips) if cloud_trips is not None else -1,
        "local_trips": local_trips,
        "cloud_trips": cloud_trips,
        "local_ids": set(t["tripId"] for t in (local_trips or [])),
        "cloud_ids": set(t["tripId"] for t in (cloud_trips or [])),
    }


async def main():
    print(f"Feed comparison: local proxy vs cloud — {DURATION}s ({DURATION // 60}m)")
    print(f"Sampling every {INTERVAL}s. Bus pairs: {BUS_PAIRS}")
    print(f"Ferry pairs: {FERRY_PAIRS}")
    print(f"{'='*80}\n")

    log: List[dict] = []
    start = time.time()
    sample_num = 0

    while time.time() - start < DURATION:
        sample_num += 1
        now = int(time.time())
        ts = datetime.now().strftime("%H:%M:%S")

        print(f"--- Sample #{sample_num} at {ts} ---")

        for feed_name, pairs in [("BUS", BUS_PAIRS), ("FERRY", FERRY_PAIRS)]:
            result = await sample_once(feed_name, pairs, now)
            log.append(result)

            lc = result["local_count"]
            cc = result["cloud_count"]
            status = ""
            if lc == -1:
                status = " [LOCAL FAILED]"
            elif cc == -1:
                status = " [CLOUD FAILED]"
            elif lc != cc:
                status = f" [DIVERGENCE: local={lc}, cloud={cc}]"

            print(f"  {feed_name:<6} local={lc:>2}  cloud={cc:>2}{status}")

            # Print trip details when we have data from both
            if result["local_trips"] and result["cloud_trips"]:
                shared = result["local_ids"] & result["cloud_ids"]
                local_only = result["local_ids"] - result["cloud_ids"]
                cloud_only = result["cloud_ids"] - result["local_ids"]

                if local_only or cloud_only:
                    print(f"         shared={len(shared)}  local_only={len(local_only)}  cloud_only={len(cloud_only)}")

                # Compare matching trips for field divergence
                local_by_id = {t["tripId"]: t for t in result["local_trips"]}
                cloud_by_id = {t["tripId"]: t for t in result["cloud_trips"]}
                for tid in shared:
                    lt = local_by_id[tid]
                    ct = cloud_by_id[tid]
                    diffs = []
                    for key in ["arrivalTime", "departureTime", "isRealtime", "headsign", "routeName"]:
                        if lt.get(key) != ct.get(key):
                            diffs.append(f"{key}: L={lt.get(key)} C={ct.get(key)}")
                    if diffs:
                        route = lt.get("routeName", "?")
                        print(f"         DIFF trip {tid[:15]} ({route}): {'; '.join(diffs)}")

            elif result["local_trips"]:
                for t in result["local_trips"][:3]:
                    print(f"    L: {fmt_trip(t, now)}")
            elif result["cloud_trips"]:
                for t in result["cloud_trips"][:3]:
                    print(f"    C: {fmt_trip(t, now)}")

        print()

        # Wait for next interval
        elapsed = time.time() - start
        remaining = DURATION - elapsed
        if remaining > INTERVAL:
            await asyncio.sleep(INTERVAL)
        elif remaining > 0:
            await asyncio.sleep(remaining)

    # --- Summary ---
    print(f"\n{'='*80}")
    print("SUMMARY")
    print(f"{'='*80}")

    for feed in ["BUS", "FERRY"]:
        entries = [e for e in log if e["feed"] == feed]
        total = len(entries)
        local_ok = sum(1 for e in entries if e["local_count"] >= 0)
        cloud_ok = sum(1 for e in entries if e["cloud_count"] >= 0)
        both_ok = [e for e in entries if e["local_count"] >= 0 and e["cloud_count"] >= 0]

        count_match = sum(1 for e in both_ok if e["local_count"] == e["cloud_count"])
        count_diverge = len(both_ok) - count_match

        local_zeros = sum(1 for e in entries if e["local_count"] == 0)
        cloud_zeros = sum(1 for e in entries if e["cloud_count"] == 0)

        avg_local = sum(e["local_count"] for e in entries if e["local_count"] >= 0) / max(local_ok, 1)
        avg_cloud = sum(e["cloud_count"] for e in entries if e["cloud_count"] >= 0) / max(cloud_ok, 1)

        print(f"\n  {feed}:")
        print(f"    Samples: {total}  (local responded: {local_ok}, cloud responded: {cloud_ok})")
        print(f"    Avg trips — local: {avg_local:.1f}, cloud: {avg_cloud:.1f}")
        print(f"    Zero-trip samples — local: {local_zeros}, cloud: {cloud_zeros}")
        if both_ok:
            print(f"    Count match: {count_match}/{len(both_ok)}  divergences: {count_diverge}")

        # Collect all field diffs
        all_diffs = {}
        for e in both_ok:
            local_by_id = {t["tripId"]: t for t in (e["local_trips"] or [])}
            cloud_by_id = {t["tripId"]: t for t in (e["cloud_trips"] or [])}
            for tid in e["local_ids"] & e["cloud_ids"]:
                lt = local_by_id[tid]
                ct = cloud_by_id[tid]
                for key in ["arrivalTime", "departureTime", "isRealtime", "headsign", "routeName"]:
                    if lt.get(key) != ct.get(key):
                        all_diffs.setdefault(key, 0)
                        all_diffs[key] += 1

        if all_diffs:
            print(f"    Field divergences across shared trips: {dict(all_diffs)}")
        else:
            print(f"    No field divergences on shared trips")

    # Dump raw log
    log_file = "/tmp/feed_comparison.json"
    serializable = []
    for e in log:
        entry = {k: v for k, v in e.items() if k not in ("local_ids", "cloud_ids")}
        serializable.append(entry)
    with open(log_file, "w") as f:
        json.dump(serializable, f, indent=2)
    print(f"\nRaw log saved to {log_file}")


if __name__ == "__main__":
    asyncio.run(main())
