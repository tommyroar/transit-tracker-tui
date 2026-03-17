#!/usr/bin/env python3
"""Compare local proxy output against direct OBA API queries.

Since the cloud endpoint (tt.horner.tj) is down, this script simulates
what the cloud WOULD return by fetching OBA data directly and applying
only the minimal cloud-equivalent logic (no effective_mode override,
no ferry direction filtering).

Samples every 90s to avoid OBA rate limits. Runs for --duration seconds.

Usage:
    uv run python scripts/compare_direct.py [--duration 1800]
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import websockets

sys.path.insert(0, "src")
from transit_tracker.transit_api import TransitAPI

LOCAL_URL = "ws://localhost:8000"

# Subscriptions: (route_id, stop_id, offset_sec, feed_label)
SUBS = [
    ("40_100240", "1_8494", -420, "BUS"),   # Route 554
    ("1_100039", "1_11920", -540, "BUS"),    # Route 14
    ("95_73", "95_7", 0, "FERRY"),            # Seattle Terminal
    ("95_73", "95_3", 0, "FERRY"),            # Bainbridge Terminal
]

DURATION = int(sys.argv[sys.argv.index("--duration") + 1]) if "--duration" in sys.argv else 1800
INTERVAL = 90  # avoid OBA rate limits


async def fetch_local(pairs: str, limit: int = 10) -> Optional[List[dict]]:
    """Get trips from local proxy."""
    try:
        async with asyncio.timeout(15):
            async with websockets.connect(LOCAL_URL) as ws:
                await ws.send(json.dumps({
                    "event": "schedule:subscribe",
                    "data": {"routeStopPairs": pairs, "limit": limit},
                }))
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if data.get("event") == "schedule":
                        return data["data"].get("trips", [])
    except Exception as e:
        print(f"  [LOCAL ERROR] {e}")
        return None


async def fetch_oba_direct(api: TransitAPI, route_id: str, stop_id: str, offset_sec: int) -> List[dict]:
    """Fetch from OBA directly and build trip objects the way the cloud proxy would.

    Cloud behavior: uses arrival time uniformly, isRealtime from predicted flag.
    No effective_mode override, no ferry direction filtering.
    """
    try:
        arrivals = await api.get_arrivals(stop_id)
    except Exception as e:
        print(f"  [OBA ERROR for {stop_id}] {e}")
        return []

    if not arrivals:
        return []

    now_ts = int(time.time())
    now_minus_buffer = now_ts - 60
    trips = []

    for arr in arrivals:
        # Match route
        arr_route = str(arr.get("routeId", ""))
        if arr_route != route_id:
            continue

        # Get times (OBA returns milliseconds)
        pred_arr = arr.get("predictedArrivalTime")
        sched_arr = arr.get("scheduledArrivalTime")
        pred_dep = arr.get("predictedDepartureTime")
        sched_dep = arr.get("scheduledDepartureTime")

        raw_arr = pred_arr if (pred_arr and pred_arr > 0) else sched_arr
        raw_dep = pred_dep if (pred_dep and pred_dep > 0) else sched_dep

        if not raw_arr:
            continue

        if raw_arr > 10**12:
            raw_arr //= 1000
        if raw_dep and raw_dep > 10**12:
            raw_dep //= 1000

        # Cloud uses arrival time uniformly (no effective_mode logic)
        base_time = raw_arr
        if not base_time or base_time < now_minus_buffer:
            continue

        final_time = base_time + offset_sec
        headsign = str(arr.get("headsign") or arr.get("tripHeadsign") or "Transit")
        route_name = str(arr.get("routeShortName") or arr.get("routeName") or "")

        trips.append({
            "tripId": str(arr.get("tripId", "")),
            "routeId": arr_route,
            "routeName": route_name,
            "routeColor": str(arr.get("routeColor", "")) if arr.get("routeColor") else None,
            "stopId": stop_id,
            "headsign": headsign,
            "arrivalTime": int(final_time),
            "departureTime": int((raw_dep or raw_arr) + offset_sec),
            "isRealtime": bool(arr.get("predicted", False)),
            # Extra diagnostic fields
            "_raw_arr": raw_arr,
            "_raw_dep": raw_dep,
            "_dep_enabled": arr.get("departureEnabled"),
            "_arr_enabled": arr.get("arrivalEnabled"),
        })

    trips.sort(key=lambda x: x["arrivalTime"])
    return trips


async def main():
    print(f"Direct OBA vs local proxy comparison — {DURATION}s ({DURATION // 60}m)")
    print(f"Sampling every {INTERVAL}s to stay under OBA rate limits")
    print(f"{'='*90}\n")

    api = TransitAPI()
    log = []
    start = time.time()
    sample_num = 0

    try:
        while time.time() - start < DURATION:
            sample_num += 1
            now = int(time.time())
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"--- Sample #{sample_num} at {ts} ---")

            # Fetch local proxy data for all pairs
            all_pairs = ";".join(f"{s[0]},{s[1]},{s[2]}" for s in SUBS)
            local_trips = await fetch_local(all_pairs)

            # Fetch OBA direct for each subscription (with spacing to avoid 429)
            oba_trips_by_feed = {"BUS": [], "FERRY": []}
            for i, (route_id, stop_id, offset, feed) in enumerate(SUBS):
                if i > 0:
                    await asyncio.sleep(2)  # space out OBA requests
                trips = await fetch_oba_direct(api, route_id, stop_id, offset)
                oba_trips_by_feed[feed].extend(trips)

            # Split local trips by feed
            local_by_feed = {"BUS": [], "FERRY": []}
            for t in (local_trips or []):
                rid = t.get("routeId", "")
                feed = "FERRY" if rid.startswith("95_") else "BUS"
                local_by_feed[feed].append(t)

            for feed in ["BUS", "FERRY"]:
                lt = local_by_feed[feed]
                ot = oba_trips_by_feed[feed]

                local_ids = {t["tripId"] for t in lt}
                oba_ids = {t["tripId"] for t in ot}
                shared = local_ids & oba_ids
                local_only = local_ids - oba_ids
                oba_only = oba_ids - local_ids

                print(f"  {feed:<6} local={len(lt):>2}  oba_direct={len(ot):>2}  "
                      f"shared={len(shared)}  local_only={len(local_only)}  oba_only={len(oba_only)}")

                # Show trips only in OBA (dropped by local proxy)
                if oba_only:
                    oba_by_id = {t["tripId"]: t for t in ot}
                    for tid in sorted(oba_only):
                        t = oba_by_id[tid]
                        diff = (t["arrivalTime"] - now) // 60
                        flags = f"arrEn={t['_arr_enabled']} depEn={t['_dep_enabled']}"
                        print(f"    DROPPED by proxy: {t['routeName']:<6} {t['headsign']:<25} "
                              f"{diff:>3}m  {flags}")

                # Compare shared trips
                local_by_id = {t["tripId"]: t for t in lt}
                oba_by_id = {t["tripId"]: t for t in ot}
                for tid in shared:
                    ltx = local_by_id[tid]
                    otx = oba_by_id[tid]
                    diffs = []
                    for key in ["arrivalTime", "isRealtime", "headsign"]:
                        if ltx.get(key) != otx.get(key):
                            diffs.append(f"{key}: local={ltx.get(key)} oba={otx.get(key)}")
                    if diffs:
                        print(f"    DIFF {ltx['routeName']:<6} {tid[:15]}: {'; '.join(diffs)}")

                entry = {
                    "time": ts,
                    "feed": feed,
                    "local_count": len(lt),
                    "oba_count": len(ot),
                    "shared": len(shared),
                    "local_only_count": len(local_only),
                    "oba_only_count": len(oba_only),
                    "local_only_ids": list(local_only),
                    "oba_only_ids": list(oba_only),
                }
                log.append(entry)

            print()

            elapsed = time.time() - start
            remaining = DURATION - elapsed
            if remaining > INTERVAL:
                await asyncio.sleep(INTERVAL)
            elif remaining > 0:
                await asyncio.sleep(remaining)

    finally:
        await api.close()

    # --- Summary ---
    print(f"\n{'='*90}")
    print("SUMMARY")
    print(f"{'='*90}")

    for feed in ["BUS", "FERRY"]:
        entries = [e for e in log if e["feed"] == feed]
        total = len(entries)
        if not total:
            continue

        avg_local = sum(e["local_count"] for e in entries) / total
        avg_oba = sum(e["oba_count"] for e in entries) / total
        total_dropped = sum(e["oba_only_count"] for e in entries)
        total_extra = sum(e["local_only_count"] for e in entries)
        samples_with_drops = sum(1 for e in entries if e["oba_only_count"] > 0)

        print(f"\n  {feed}:")
        print(f"    Samples: {total}")
        print(f"    Avg trips — local: {avg_local:.1f}, oba_direct: {avg_oba:.1f}")
        print(f"    Trips dropped by proxy (in OBA but not local): {total_dropped} across {samples_with_drops} samples")
        print(f"    Trips extra in proxy (in local but not OBA): {total_extra}")

    log_file = "/tmp/direct_comparison.json"
    with open(log_file, "w") as f:
        json.dump(log, f, indent=2)
    print(f"\nRaw log saved to {log_file}")


if __name__ == "__main__":
    asyncio.run(main())
