"""Capture cloud vs local proxy traffic side-by-side for the same subscription.

Why this exists
---------------
The ESP32 firmware has a stale-trip reconnect rule (components/transit_tracker/
transit_tracker.cpp ~L26-53): every 10s it checks every trip's ``departure_time``
and — if ALL trips have ``now - departure_time > 60`` — it disconnects and
reconnects. Pair that with our local proxy, which filters with a 60-second
grace window *before* applying the user-configured time offset, and a negative
offset (e.g. -540s / "9 min walking time") can push every trip's broadcast
``departureTime`` well below ``now - 60`` — the reconnect storm condition.

The reference cloud proxy (tjhorner/transit-tracker-api, src/schedule/
schedule.service.ts ~L91) filters ``trip[sortKey] > now`` *after* offset is
applied, where ``sortKey`` is ``departureTime`` when the firmware sends
``sortByDeparture: true`` and ``arrivalTime`` otherwise. So the cloud's
contract is: every trip in the broadcast has a future ``departureTime``.

This harness subscribes to both endpoints with an IDENTICAL handshake that
matches what the firmware actually sends (see esphome-transit-tracker,
transit_tracker.cpp ~L165-178), records every inbound frame with timestamps,
and flags any broadcast that would trip the stale-check rule on-device.

Run it
------
    uv run python scripts/capture_proxy_diff.py \\
        --pairs "st:40_100240,st:1_8494,-420;st:1_100039,st:1_11920,-540" \\
        --limit 5 --sort-by-departure --duration 180

Outputs
-------
  /tmp/proxy-diff-<ts>/cloud.jsonl    — all frames received from wss://tt.horner.tj
  /tmp/proxy-diff-<ts>/local.jsonl    — all frames received from ws://localhost:8000
  /tmp/proxy-diff-<ts>/report.txt     — side-by-side summary + reconnect-risk flags
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import websockets

CLOUD_URL = "wss://tt.horner.tj/"
LOCAL_URL = "ws://localhost:8000"

# Firmware stale-check threshold (esphome-transit-tracker/transit_tracker.cpp L35)
STALE_GRACE_SEC = 60


@dataclass
class Capture:
    label: str
    url: str
    outpath: str
    frames: list[dict[str, Any]] = field(default_factory=list)
    connect_error: str | None = None


def _build_handshake(
    pairs: str,
    limit: int,
    sort_by_departure: bool,
    list_mode: str,
    feed_code: str | None,
    client_name: str,
) -> dict[str, Any]:
    """Mirror firmware's schedule:subscribe payload (transit_tracker.cpp L165-178)."""
    data: dict[str, Any] = {
        "routeStopPairs": pairs,
        "limit": limit,
        "sortByDeparture": sort_by_departure,
        "listMode": list_mode,
    }
    if feed_code:
        data["feedCode"] = feed_code
    return {
        "event": "schedule:subscribe",
        "client_name": client_name,
        "data": data,
    }


async def _record(capture: Capture, handshake: dict[str, Any], duration: float) -> None:
    deadline = time.time() + duration
    try:
        async with websockets.connect(capture.url, open_timeout=10, close_timeout=2) as ws:
            t_send = time.time()
            await ws.send(json.dumps(handshake))
            capture.frames.append({
                "ts": t_send,
                "direction": "out",
                "payload": handshake,
            })
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                ts = time.time()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"_raw_nonjson": raw}
                capture.frames.append({"ts": ts, "direction": "in", "payload": payload})
    except Exception as e:
        capture.connect_error = f"{type(e).__name__}: {e}"

    with open(capture.outpath, "w") as f:
        for frame in capture.frames:
            f.write(json.dumps(frame) + "\n")


def _schedule_frames(capture: Capture) -> list[dict[str, Any]]:
    return [
        f for f in capture.frames
        if f["direction"] == "in" and f["payload"].get("event") == "schedule"
    ]


def _heartbeats(capture: Capture) -> list[dict[str, Any]]:
    return [
        f for f in capture.frames
        if f["direction"] == "in" and f["payload"].get("event") == "heartbeat"
    ]


def _reconnect_risk(capture: Capture) -> list[tuple[float, int, int]]:
    """Flag broadcasts where EVERY trip has `departureTime < now - 60`.

    Returns list of (frame_ts, trip_count, stale_count).
    """
    risks = []
    for frame in _schedule_frames(capture):
        trips = frame["payload"].get("data", {}).get("trips", [])
        if not trips:
            continue
        now = frame["ts"]
        stale = sum(
            1 for t in trips
            if (t.get("departureTime") or 0) <= now - STALE_GRACE_SEC
        )
        if stale == len(trips):
            risks.append((frame["ts"], len(trips), stale))
    return risks


def _first_schedule(capture: Capture) -> dict[str, Any] | None:
    frames = _schedule_frames(capture)
    return frames[0]["payload"] if frames else None


def _summarize_trips(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    if not schedule:
        return []
    return schedule.get("data", {}).get("trips", [])


def _fmt_trip(t: dict[str, Any], now: float) -> str:
    arr = t.get("arrivalTime") or 0
    dep = t.get("departureTime") or 0
    return (
        f"  trip={t.get('tripId'):<20} route={t.get('routeName'):<6} "
        f"arr={int(arr - now):+5d}s dep={int(dep - now):+5d}s "
        f"rt={'Y' if t.get('isRealtime') else 'N'}"
    )


def _write_report(outdir: str, captures: list[Capture], args: argparse.Namespace) -> str:
    lines: list[str] = []
    w = lines.append
    ts_iso = datetime.now(timezone.utc).isoformat()
    w(f"=== Proxy diff capture — {ts_iso} ===")
    w(f"Handshake: pairs={args.pairs!r} limit={args.limit} "
      f"sortByDeparture={args.sort_by_departure} listMode={args.list_mode!r} "
      f"feedCode={args.feed_code!r}")
    w(f"Duration:  {args.duration}s")
    w("")

    for cap in captures:
        w(f"--- {cap.label} ({cap.url}) ---")
        if cap.connect_error:
            w(f"  CONNECT ERROR: {cap.connect_error}")
            w("")
            continue
        scheds = _schedule_frames(cap)
        beats = _heartbeats(cap)
        w(f"  frames:       {len(cap.frames)}")
        w(f"  schedule:     {len(scheds)} broadcasts")
        w(f"  heartbeat:    {len(beats)}")
        if scheds:
            deltas = [scheds[i]["ts"] - scheds[i-1]["ts"] for i in range(1, len(scheds))]
            if deltas:
                w(f"  schedule Δt:  min={min(deltas):.1f}s max={max(deltas):.1f}s "
                  f"avg={sum(deltas)/len(deltas):.1f}s")
        if beats:
            bdeltas = [beats[i]["ts"] - beats[i-1]["ts"] for i in range(1, len(beats))]
            if bdeltas:
                w(f"  heartbeat Δt: min={min(bdeltas):.1f}s max={max(bdeltas):.1f}s "
                  f"avg={sum(bdeltas)/len(bdeltas):.1f}s")
        risks = _reconnect_risk(cap)
        if risks:
            w(f"  !! STALE-ALL broadcasts (would trip firmware reconnect): {len(risks)}")
            for rts, ntrips, _ in risks[:5]:
                w(f"       @ {datetime.fromtimestamp(rts).isoformat(timespec='seconds')} "
                  f"trips={ntrips} all past-by-60s")
        else:
            w(f"  stale-all:    0 (no reconnect-storm condition observed)")
        w("")

    cloud = next((c for c in captures if c.label == "cloud"), None)
    local = next((c for c in captures if c.label == "local"), None)

    if cloud and local and not cloud.connect_error and not local.connect_error:
        w("=== First-schedule side-by-side ===")
        cloud_sched = _first_schedule(cloud)
        local_sched = _first_schedule(local)
        cloud_trips = _summarize_trips(cloud_sched) if cloud_sched else []
        local_trips = _summarize_trips(local_sched) if local_sched else []
        now = time.time()

        w(f"CLOUD trips ({len(cloud_trips)}):")
        for t in cloud_trips:
            w(_fmt_trip(t, now))
        w(f"LOCAL trips ({len(local_trips)}):")
        for t in local_trips:
            w(_fmt_trip(t, now))

        cloud_keys = set(cloud_trips[0].keys()) if cloud_trips else set()
        local_keys = set(local_trips[0].keys()) if local_trips else set()
        if cloud_keys and local_keys:
            missing = cloud_keys - local_keys
            extra = local_keys - cloud_keys
            w("")
            w(f"Trip-field delta: missing_in_local={sorted(missing)} extra_in_local={sorted(extra)}")

        cloud_ids = {t["tripId"] for t in cloud_trips}
        local_ids = {t["tripId"] for t in local_trips}
        shared = cloud_ids & local_ids
        w(f"Overlapping tripIds: {len(shared)} of {len(cloud_ids)} cloud / {len(local_ids)} local")

        sort_key = "departureTime" if args.sort_by_departure else "arrivalTime"
        cloud_past = [t for t in cloud_trips if (t.get(sort_key) or 0) <= now]
        local_past = [t for t in local_trips if (t.get(sort_key) or 0) <= now]
        w(f"Past-{sort_key} trips:  cloud={len(cloud_past)}  local={len(local_past)}  "
          f"(cloud filters these out — {sort_key} > now)")

        local_past_by_60 = [
            t for t in local_trips if (t.get("departureTime") or 0) <= now - STALE_GRACE_SEC
        ]
        if local_past_by_60 and len(local_past_by_60) == len(local_trips) and local_trips:
            w("")
            w("!!! LOCAL first broadcast would trip firmware reconnect:")
            w(f"    all {len(local_trips)} trips have departureTime < now - {STALE_GRACE_SEC}s")

    report_path = os.path.join(outdir, "report.txt")
    with open(report_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    return report_path


async def _main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", default="st:40_100240,st:1_8494,-420;st:1_100039,st:1_11920,-540",
                    help="routeStopPairs handshake string")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--sort-by-departure", action="store_true",
                    help="Send sortByDeparture=true (matches firmware when time_display=departure)")
    ap.add_argument("--list-mode", default="sequential", choices=["sequential", "nextPerRoute"])
    ap.add_argument("--feed-code", default=None)
    ap.add_argument("--client-name", default="ProxyDiffCapture")
    ap.add_argument("--duration", type=float, default=180.0,
                    help="Seconds to record (>= 3 schedule intervals recommended)")
    ap.add_argument("--cloud-url", default=CLOUD_URL)
    ap.add_argument("--local-url", default=LOCAL_URL)
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()

    outdir = args.outdir or f"/tmp/proxy-diff-{time.strftime('%Y%m%d-%H%M%S')}"
    os.makedirs(outdir, exist_ok=True)

    handshake = _build_handshake(
        pairs=args.pairs,
        limit=args.limit,
        sort_by_departure=args.sort_by_departure,
        list_mode=args.list_mode,
        feed_code=args.feed_code,
        client_name=args.client_name,
    )

    captures = [
        Capture("cloud", args.cloud_url, os.path.join(outdir, "cloud.jsonl")),
        Capture("local", args.local_url, os.path.join(outdir, "local.jsonl")),
    ]

    print(f"Recording {args.duration}s from cloud + local to {outdir}")
    await asyncio.gather(*(_record(c, handshake, args.duration) for c in captures))

    report = _write_report(outdir, captures, args)
    print(f"\nReport: {report}")
    print(f"Raw:    {captures[0].outpath}")
    print(f"        {captures[1].outpath}")
    with open(report) as f:
        sys.stdout.write(f.read())
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
