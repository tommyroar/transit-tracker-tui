"""Transit Tracker Equivalence Verifier.

Modes:
  (default)      Compare a locally running service against the cloud baseline.
  --containers   Compare the local transit-tracker Docker container against the
                 reference container (ghcr.io/tjhorner/transit-tracker-api).

Usage:
  python scripts/verify_cloud_equivalence.py              # local vs cloud
  python scripts/verify_cloud_equivalence.py --containers  # container vs container
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
from typing import Any, Dict, Optional

import websockets

CLOUD_URL = "wss://tt.horner.tj/"
LOCAL_URL = "ws://localhost:8000"

# Standard test pairs
HANDSHAKE_PAIRS = "40_100240,1_8494,-420;1_100039,1_11920,-540"

# Container comparison settings
REFERENCE_IMAGE = "ghcr.io/tjhorner/transit-tracker-api"
LOCAL_IMAGE = "transit-tracker"
REFERENCE_CONTAINER = "equiv-reference"
LOCAL_CONTAINER = "equiv-local"
REFERENCE_HOST_PORT = 13000
LOCAL_HOST_PORT = 18000
CONNECT_TIMEOUT = 120  # seconds


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

async def get_update(url: str, name: str, timeout: int = 120) -> Optional[Dict[str, Any]]:
    """Connects to a proxy and waits for the first schedule update."""
    print(f"[{name}] Connecting to {url}...")
    try:
        async with asyncio.timeout(timeout):
            async with websockets.connect(url) as ws:
                handshake = {
                    "event": "schedule:subscribe",
                    "data": {
                        "routeStopPairs": HANDSHAKE_PAIRS,
                        "limit": 5,
                    },
                }
                await ws.send(json.dumps(handshake))

                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if data.get("event") == "schedule":
                        return data["data"]
    except Exception as e:
        print(f"[{name}] Error: {e}")
        return None


def format_trip(t: Dict[str, Any]) -> str:
    arr = t.get("arrivalTime", 0)
    now = int(time.time())
    diff = (arr - now) // 60
    is_live = "✅" if t.get("isRealtime") else "❌"
    return (
        f"{str(t.get('routeName', '')):<10} | "
        f"{str(t.get('headsign', '')):<25} | "
        f"{diff:>3}m | {is_live:<5} | {t.get('tripId')}"
    )


def print_trips(label: str, data: Dict[str, Any]) -> None:
    trips = data.get("trips", [])
    print(f"{label} ({len(trips)} trips):")
    for t in trips:
        print(f"  {format_trip(t)}")


def structural_comparison(
    a_data: Dict[str, Any],
    b_data: Dict[str, Any],
    a_label: str,
    b_label: str,
) -> None:
    """Compare top-level keys and per-trip field names between two responses."""
    print("\n" + "=" * 60)
    print("STRUCTURAL VALIDATION")

    a_keys = set(a_data.keys())
    b_keys = set(b_data.keys())

    if a_keys == b_keys:
        print(f"✅ Top-level schema matches ({a_label} vs {b_label})")
    else:
        print(f"❌ Schema mismatch!")
        print(f"   {a_label} keys: {sorted(a_keys)}")
        print(f"   {b_label} keys: {sorted(b_keys)}")
        missing_in_a = b_keys - a_keys
        extra_in_a = a_keys - b_keys
        if missing_in_a:
            print(f"   Missing in {a_label}: {missing_in_a}")
        if extra_in_a:
            print(f"   Extra in {a_label}: {extra_in_a}")

    a_trips = a_data.get("trips", [])
    b_trips = b_data.get("trips", [])

    if a_trips and b_trips:
        a_fields = set(a_trips[0].keys())
        b_fields = set(b_trips[0].keys())
        if a_fields == b_fields:
            print("✅ Trip object fields match exactly")
        else:
            print("❌ Trip field divergence detected!")
            missing = b_fields - a_fields
            extra = a_fields - b_fields
            if missing:
                print(f"   Missing in {a_label}: {missing}")
            if extra:
                print(f"   Extra in {a_label}: {extra}")
    elif not a_trips:
        print(f"⚠️  No trips from {a_label} — cannot compare trip fields")
    elif not b_trips:
        print(f"⚠️  No trips from {b_label} — cannot compare trip fields")


# ---------------------------------------------------------------------------
# Local-vs-cloud mode (original behavior)
# ---------------------------------------------------------------------------

async def local_vs_cloud() -> None:
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

    print("\n" + "=" * 60)
    print(f"{'ROUTE':<10} | {'HEADSIGN':<25} | {'DUE':<5} | {'LIVE':<5} | {'ID'}")
    print("-" * 60)

    print_trips("LOCAL PROXY", local_data)

    if cloud_data:
        print()
        print_trips("CLOUD PROXY", cloud_data)
        structural_comparison(local_data, cloud_data, "LOCAL", "CLOUD")


# ---------------------------------------------------------------------------
# Container-vs-container mode
# ---------------------------------------------------------------------------

def _run(cmd: list[str], *, check: bool = True, timeout: int = 300, **kwargs) -> subprocess.CompletedProcess:
    """Run a subprocess with defaults suitable for Docker commands."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check, timeout=timeout, **kwargs)


def _container_cleanup() -> None:
    """Remove both equivalence-test containers if they exist."""
    for name in (REFERENCE_CONTAINER, LOCAL_CONTAINER):
        subprocess.run(
            ["docker", "rm", "-f", name],
            capture_output=True,
            timeout=30,
        )


def _ensure_image(image: str) -> None:
    """Pull an image if it is not present locally."""
    result = subprocess.run(
        ["docker", "image", "inspect", image],
        capture_output=True,
        timeout=30,
    )
    if result.returncode != 0:
        print(f"Pulling {image}...")
        _run(["docker", "pull", image], timeout=600)
    else:
        print(f"Image {image} already present.")


def _wait_for_ws(port: int, label: str, timeout: int = CONNECT_TIMEOUT) -> bool:
    """Block until a WebSocket on *port* accepts a connection, or timeout."""
    import websockets.sync.client

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with websockets.sync.client.connect(
                f"ws://localhost:{port}",
                open_timeout=2,
                close_timeout=1,
            ):
                return True
        except Exception:
            time.sleep(1)
    return False



def _start_containers() -> bool:
    """Start both the reference and local containers. Returns True on success."""
    _container_cleanup()

    # --- Reference container (port 3000 inside, mapped to REFERENCE_HOST_PORT) ---
    print(f"\nStarting reference container on :{REFERENCE_HOST_PORT}...")
    _run([
        "docker", "run", "-d",
        "--name", REFERENCE_CONTAINER,
        "-p", f"{REFERENCE_HOST_PORT}:3000",
        REFERENCE_IMAGE,
    ])

    # --- Local container (WS on 8000 inside, mapped to LOCAL_HOST_PORT) ---
    print(f"Starting local container on :{LOCAL_HOST_PORT}...")
    _run([
        "docker", "run", "-d",
        "--name", LOCAL_CONTAINER,
        "-p", f"{LOCAL_HOST_PORT}:8000",
        LOCAL_IMAGE,
    ])

    # Wait for both to accept WebSocket connections
    print("Waiting for containers to accept WebSocket connections...")
    ref_ready = _wait_for_ws(REFERENCE_HOST_PORT, "REFERENCE")
    local_ready = _wait_for_ws(LOCAL_HOST_PORT, "LOCAL")

    if not ref_ready:
        print(f"⚠️  Reference container did not accept connections within {CONNECT_TIMEOUT}s")
        logs = subprocess.run(
            ["docker", "logs", REFERENCE_CONTAINER],
            capture_output=True, text=True, timeout=10,
        )
        print(f"   Logs: {logs.stdout[-500:] if logs.stdout else '(empty)'}")
    if not local_ready:
        print(f"⚠️  Local container did not accept connections within {CONNECT_TIMEOUT}s")
        logs = subprocess.run(
            ["docker", "logs", LOCAL_CONTAINER],
            capture_output=True, text=True, timeout=10,
        )
        print(f"   Logs: {logs.stdout[-500:] if logs.stdout else '(empty)'}")

    return ref_ready and local_ready


async def container_vs_container() -> None:
    """Compare the local transit-tracker container against the reference container."""
    print("🐳 Transit Tracker Container Equivalence Verifier")
    print("Comparing local container against reference container...\n")

    # 1. Ensure images exist
    _ensure_image(REFERENCE_IMAGE)
    result = subprocess.run(
        ["docker", "image", "inspect", LOCAL_IMAGE],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0:
        print(f"❌ Local image '{LOCAL_IMAGE}' not found. Build it first:")
        print(f"   docker build -t {LOCAL_IMAGE} .")
        sys.exit(1)

    # 2. Start containers
    try:
        both_ready = _start_containers()

        if not both_ready:
            print("\n⚠️  TIMEOUT: One or both containers did not start in time.")
            print("   Skipping data comparison.")
            return

        # 3. Fetch schedule data from both
        ref_url = f"ws://localhost:{REFERENCE_HOST_PORT}"
        local_url = f"ws://localhost:{LOCAL_HOST_PORT}"

        ref_data = await get_update(ref_url, "REFERENCE", timeout=CONNECT_TIMEOUT)
        local_data = await get_update(local_url, "LOCAL", timeout=CONNECT_TIMEOUT)

        # 4. Report
        print("\n" + "=" * 60)
        print(f"{'ROUTE':<10} | {'HEADSIGN':<25} | {'DUE':<5} | {'LIVE':<5} | {'ID'}")
        print("-" * 60)

        if local_data:
            print_trips("LOCAL CONTAINER", local_data)
        else:
            print("❌ No data from local container")

        if ref_data:
            print()
            print_trips("REFERENCE CONTAINER", ref_data)
        else:
            print("\n⚠️  No data from reference container (timeout or error)")
            print("   This may be due to rate limiting or network issues.")

        # 5. Structural comparison
        if local_data and ref_data:
            structural_comparison(local_data, ref_data, "LOCAL", "REFERENCE")

            # Summary report
            local_trips = local_data.get("trips", [])
            ref_trips = ref_data.get("trips", [])
            print("\n" + "=" * 60)
            print("EQUIVALENCE SUMMARY")
            print(f"  Local trip count:     {len(local_trips)}")
            print(f"  Reference trip count: {len(ref_trips)}")

            top_match = set(local_data.keys()) == set(ref_data.keys())
            field_match = False
            divergences: list[str] = []

            if local_trips and ref_trips:
                l_fields = set(local_trips[0].keys())
                r_fields = set(ref_trips[0].keys())
                field_match = l_fields == r_fields
                if not field_match:
                    missing = r_fields - l_fields
                    extra = l_fields - r_fields
                    if missing:
                        divergences.append(f"Missing in local: {missing}")
                    if extra:
                        divergences.append(f"Extra in local: {extra}")

            print(f"  Top-level keys match: {'✅' if top_match else '❌'}")
            print(f"  Trip fields match:    {'✅' if field_match else '❌' if (local_trips and ref_trips) else '⚠️  N/A'}")
            if divergences:
                print("  Divergences:")
                for d in divergences:
                    print(f"    - {d}")
            else:
                print("  Divergences:          None")
        elif local_data and not ref_data:
            print("\n⚠️  Cannot compare — reference container returned no data.")
            print("   Local data is shown above for manual review.")
        elif not local_data:
            print("\n❌ FAILED: Local container returned no data.")
            sys.exit(1)

    finally:
        # Always clean up containers
        print("\nCleaning up containers...")
        _container_cleanup()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Transit Tracker Equivalence Verifier",
    )
    parser.add_argument(
        "--containers",
        action="store_true",
        help="Compare local Docker container against the reference container "
             "(instead of local service vs cloud)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        if args.containers:
            asyncio.run(container_vs_container())
        else:
            asyncio.run(local_vs_cloud())
    except KeyboardInterrupt:
        pass
