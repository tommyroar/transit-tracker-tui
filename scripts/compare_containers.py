#!/usr/bin/env python3
"""A/B comparison: reference container vs our Python proxy in Docker.

Samples both containerized endpoints every 90s, logs divergences per feed,
and prints a summary table. Categorizes divergences as expected (ferry logic)
or unexpected (bus should match).

Usage:
    uv run python scripts/compare_containers.py [--duration 1800]
"""

import asyncio
import json
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import websockets

REFERENCE_URL = "ws://localhost:3000"
PROXY_URL = "ws://localhost:8000"

# Reference container uses feed-prefixed IDs
REF_BUS_PAIRS = (
    "puget_sound:40_100240,puget_sound:1_8494"
    ";puget_sound:1_100039,puget_sound:1_11920"
)
REF_FERRY_PAIRS = (
    "puget_sound:95_73,puget_sound:95_7"
    ";puget_sound:95_73,puget_sound:95_3"
)

# Our proxy uses bare IDs with offset
PROXY_BUS_PAIRS = "40_100240,1_8494,0;1_100039,1_11920,0"
PROXY_FERRY_PAIRS = "95_73,95_7,0;95_73,95_3,0"

DURATION = (
    int(sys.argv[sys.argv.index("--duration") + 1])
    if "--duration" in sys.argv
    else 1800
)
INTERVAL = 90  # seconds between samples — reduce OBA rate limit pressure


async def fetch_trips(
    url: str, pairs: str, timeout: float = 30
) -> Optional[List[Dict[str, Any]]]:
    """Connect, subscribe, wait for first schedule push, disconnect."""
    try:
        async with asyncio.timeout(timeout):
            async with websockets.connect(url) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "event": "schedule:subscribe",
                            "data": {"routeStopPairs": pairs, "limit": 10},
                        }
                    )
                )
                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if data.get("event") == "schedule":
                        return data["data"].get("trips", [])
    except Exception as e:
        print(f"    [ERROR] {url}: {e}")
        return None


def normalize_trip_id(trip_id: str) -> str:
    """Strip feed prefix from trip IDs for cross-container matching.

    Reference returns e.g. 'puget_sound:1_12345', proxy returns '1_12345'.
    """
    if ":" in trip_id:
        return trip_id.split(":", 1)[1]
    return trip_id


def is_ferry_route(route_id: str) -> bool:
    """Check if a route ID belongs to a ferry route."""
    clean = route_id.split(":", 1)[-1] if ":" in route_id else route_id
    return clean.startswith("95_")


async def sample_once(
    feed_name: str, ref_pairs: str, proxy_pairs: str, now: int
) -> dict:
    """Fetch from both containers for one feed."""
    ref_task = asyncio.create_task(fetch_trips(REFERENCE_URL, ref_pairs))
    proxy_task = asyncio.create_task(fetch_trips(PROXY_URL, proxy_pairs))

    ref_trips = await ref_task
    proxy_trips = await proxy_task

    # Normalize trip IDs for comparison
    ref_by_id = {}
    if ref_trips:
        for t in ref_trips:
            ref_by_id[normalize_trip_id(t["tripId"])] = t

    proxy_by_id = {}
    if proxy_trips:
        for t in proxy_trips:
            proxy_by_id[normalize_trip_id(t["tripId"])] = t

    return {
        "time": datetime.now().strftime("%H:%M:%S"),
        "feed": feed_name,
        "ref_count": len(ref_trips) if ref_trips is not None else -1,
        "proxy_count": len(proxy_trips) if proxy_trips is not None else -1,
        "ref_trips": ref_trips,
        "proxy_trips": proxy_trips,
        "ref_ids": set(ref_by_id.keys()),
        "proxy_ids": set(proxy_by_id.keys()),
        "ref_by_id": ref_by_id,
        "proxy_by_id": proxy_by_id,
    }


def categorize_divergence(feed: str, field: str, ref_val: Any, proxy_val: Any) -> str:
    """Categorize a field divergence as expected or unexpected."""
    if feed == "FERRY":
        # Vessel name headsigns, direction filtering, effective_mode — all expected
        if field in ("headsign", "arrivalTime", "departureTime"):
            return "expected_ferry"
    if field == "routeName":
        # Abbreviation differences are expected for all feeds
        return "expected_abbreviation"
    return "unexpected"


async def main():
    print(f"Container A/B comparison — {DURATION}s ({DURATION // 60}m)")
    print(f"Reference: {REFERENCE_URL}  |  Proxy: {PROXY_URL}")
    print(f"Sampling every {INTERVAL}s")
    print(f"{'=' * 80}\n")

    log: List[dict] = []
    stats = {
        "total_samples": 0,
        "ref_failures": 0,
        "proxy_failures": 0,
        "rate_limit_suspected": 0,
    }
    prev_counts: Dict[str, int] = {}
    start = time.time()
    sample_num = 0

    while time.time() - start < DURATION:
        sample_num += 1
        now = int(time.time())
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"--- Sample #{sample_num} at {ts} ---")

        feeds = [
            ("BUS", REF_BUS_PAIRS, PROXY_BUS_PAIRS),
            ("FERRY", REF_FERRY_PAIRS, PROXY_FERRY_PAIRS),
        ]

        for feed_name, ref_pairs, proxy_pairs in feeds:
            stats["total_samples"] += 1
            result = await sample_once(feed_name, ref_pairs, proxy_pairs, now)
            log.append(result)

            rc = result["ref_count"]
            pc = result["proxy_count"]
            status = ""

            if rc == -1:
                status = " [REF FAILED]"
                stats["ref_failures"] += 1
            elif pc == -1:
                status = " [PROXY FAILED]"
                stats["proxy_failures"] += 1
            elif rc != pc:
                status = f" [COUNT: ref={rc}, proxy={pc}]"

            # Rate limit detection: previously had trips, now zero
            for label, count in [("ref", rc), ("proxy", pc)]:
                key = f"{feed_name}_{label}"
                if prev_counts.get(key, 0) > 0 and count == 0:
                    status += f" [RATE-LIMIT? {label}]"
                    stats["rate_limit_suspected"] += 1
                prev_counts[key] = count if count >= 0 else 0

            print(f"  {feed_name:<6} ref={rc:>2}  proxy={pc:>2}{status}")

            # Compare trips when both responded
            if result["ref_trips"] and result["proxy_trips"]:
                shared = result["ref_ids"] & result["proxy_ids"]
                ref_only = result["ref_ids"] - result["proxy_ids"]
                proxy_only = result["proxy_ids"] - result["ref_ids"]

                if ref_only or proxy_only:
                    print(
                        f"         shared={len(shared)}  "
                        f"ref_only={len(ref_only)}  "
                        f"proxy_only={len(proxy_only)}"
                    )
                    if ref_only and feed_name == "FERRY":
                        print(
                            "         ^ expected: proxy filters ferry by direction"
                        )

                # Field-level comparison on shared trips
                for tid in sorted(shared):
                    rt = result["ref_by_id"][tid]
                    pt = result["proxy_by_id"][tid]
                    diffs = []
                    for key in [
                        "arrivalTime",
                        "departureTime",
                        "isRealtime",
                        "headsign",
                        "routeName",
                    ]:
                        rv = rt.get(key)
                        pv = pt.get(key)
                        if rv != pv:
                            cat = categorize_divergence(feed_name, key, rv, pv)
                            diffs.append(f"{key}: R={rv} P={pv} [{cat}]")
                    if diffs:
                        route = pt.get("routeName", "?")
                        print(
                            f"         DIFF {tid[:20]} ({route}): "
                            f"{'; '.join(diffs)}"
                        )

        print()

        # Wait for next interval
        elapsed = time.time() - start
        remaining = DURATION - elapsed
        if remaining > INTERVAL:
            await asyncio.sleep(INTERVAL)
        elif remaining > 0:
            await asyncio.sleep(remaining)

    # --- Summary ---
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    print(f"  Total samples: {stats['total_samples']}")
    print(f"  Ref failures: {stats['ref_failures']}")
    print(f"  Proxy failures: {stats['proxy_failures']}")
    print(f"  Suspected rate limits: {stats['rate_limit_suspected']}")

    for feed in ["BUS", "FERRY"]:
        entries = [e for e in log if e["feed"] == feed]
        total = len(entries)
        ref_ok = sum(1 for e in entries if e["ref_count"] >= 0)
        proxy_ok = sum(1 for e in entries if e["proxy_count"] >= 0)
        both_ok = [
            e for e in entries if e["ref_count"] >= 0 and e["proxy_count"] >= 0
        ]

        count_match = sum(
            1 for e in both_ok if e["ref_count"] == e["proxy_count"]
        )

        avg_ref = (
            sum(e["ref_count"] for e in entries if e["ref_count"] >= 0)
            / max(ref_ok, 1)
        )
        avg_proxy = (
            sum(e["proxy_count"] for e in entries if e["proxy_count"] >= 0)
            / max(proxy_ok, 1)
        )

        # Collect all divergences by category
        divergences: Dict[str, int] = {}
        total_dropped = 0
        total_extra = 0

        for e in both_ok:
            ref_only = e["ref_ids"] - e["proxy_ids"]
            proxy_only = e["proxy_ids"] - e["ref_ids"]
            total_dropped += len(ref_only)
            total_extra += len(proxy_only)

            for tid in e["ref_ids"] & e["proxy_ids"]:
                rt = e["ref_by_id"][tid]
                pt = e["proxy_by_id"][tid]
                for key in [
                    "arrivalTime",
                    "departureTime",
                    "isRealtime",
                    "headsign",
                    "routeName",
                ]:
                    rv = rt.get(key)
                    pv = pt.get(key)
                    if rv != pv:
                        cat = categorize_divergence(feed, key, rv, pv)
                        divergences[cat] = divergences.get(cat, 0) + 1

        print(f"\n  {feed}:")
        print(
            f"    Samples: {total}  "
            f"(ref ok: {ref_ok}, proxy ok: {proxy_ok})"
        )
        print(f"    Avg trips — ref: {avg_ref:.1f}, proxy: {avg_proxy:.1f}")
        print(
            f"    Count match: {count_match}/{len(both_ok)}"
        )
        print(
            f"    Trips dropped by proxy: {total_dropped}  "
            f"extra in proxy: {total_extra}"
        )
        if divergences:
            print(f"    Divergences by category: {dict(divergences)}")
        else:
            print("    No field divergences on shared trips")

    # Dump raw log
    log_file = "/tmp/container_comparison.json"
    serializable = []
    for e in log:
        entry = {
            k: v
            for k, v in e.items()
            if k not in ("ref_ids", "proxy_ids", "ref_by_id", "proxy_by_id")
        }
        serializable.append(entry)
    with open(log_file, "w") as f:
        json.dump(serializable, f, indent=2)
    print(f"\nRaw log saved to {log_file}")


if __name__ == "__main__":
    asyncio.run(main())
