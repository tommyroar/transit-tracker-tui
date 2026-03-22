"""Integration test: local proxy vs cloud endpoint structural equivalence.

Connects to both wss://tt.horner.tj (cloud) and ws://localhost:8000 (local)
with the same subscriptions, and verifies the response schema and trip data
are structurally identical.

Requires both the local proxy and internet access. Marked as e2e — excluded
from CI unit/integration runs via ``-m "not e2e"``.
"""

import json
import time

import pytest
import websockets.sync.client

pytestmark = pytest.mark.e2e
CLOUD_URL = "wss://tt.horner.tj/"
LOCAL_URL = "ws://localhost:8000"

# Use the home.yaml subscriptions: 554 (st:40_100240 at st:1_8494) and 14 (st:1_100039 at st:1_11920)
HANDSHAKE_PAIRS = "st:40_100240,st:1_8494,-420;st:1_100039,st:1_11920,-540"


def fetch_one_update(url: str, timeout_s: int = 15) -> dict | None:
    """Subscribe and return the first schedule update from a WebSocket endpoint."""
    try:
        with websockets.sync.client.connect(url, close_timeout=2) as ws:
            ws.send(json.dumps({
                "event": "schedule:subscribe",
                "client_name": "EquivalenceTest",
                "data": {"routeStopPairs": HANDSHAKE_PAIRS, "limit": 6}
            }))
            msg = ws.recv(timeout=timeout_s)
            data = json.loads(msg)
            if data.get("event") == "schedule":
                return data
    except Exception:
        return None


_cache = {}

@pytest.fixture(scope="module")
def cloud_response():
    if "cloud" not in _cache:
        _cache["cloud"] = fetch_one_update(CLOUD_URL, timeout_s=30)
    return _cache["cloud"]


@pytest.fixture(scope="module")
def local_response():
    if "local" not in _cache:
        _cache["local"] = fetch_one_update(LOCAL_URL, timeout_s=10)
    return _cache["local"]


def test_local_proxy_responds(local_response):
    """Local proxy must return a schedule event."""
    assert local_response is not None, "Local proxy at ws://localhost:8000 did not respond"
    assert local_response["event"] == "schedule"
    assert "data" in local_response
    assert "trips" in local_response["data"]



def test_cloud_proxy_responds(cloud_response):
    """Cloud proxy must return a schedule event."""
    assert cloud_response is not None, "Cloud proxy at wss://tt.horner.tj did not respond"
    assert cloud_response["event"] == "schedule"



def test_top_level_schema_matches(local_response, cloud_response):
    """The top-level response structure must match between cloud and local."""
    if not cloud_response or not local_response:
        pytest.skip("One or both endpoints unreachable")
    assert set(local_response.keys()) == set(cloud_response.keys()), (
        f"Schema mismatch: local={set(local_response.keys())}, cloud={set(cloud_response.keys())}"
    )



def test_trip_schema_matches(local_response, cloud_response):
    """Every trip object must have the same set of keys."""
    if not cloud_response or not local_response:
        pytest.skip("One or both endpoints unreachable")

    local_trips = local_response["data"]["trips"]
    cloud_trips = cloud_response["data"]["trips"]

    if not local_trips:
        pytest.skip("Local proxy returned no trips (service may be rate-limited)")
    if not cloud_trips:
        pytest.skip("Cloud proxy returned no trips")

    local_keys = set(local_trips[0].keys())
    cloud_keys = set(cloud_trips[0].keys())

    missing = cloud_keys - local_keys
    extra = local_keys - cloud_keys

    assert not missing, f"Local trips missing cloud fields: {missing}"
    # Extra fields in local are acceptable (superset is OK), but flag them
    if extra:
        print(f"Note: local trips have extra fields not in cloud: {extra}")



def test_trip_field_types_match(local_response, cloud_response):
    """Trip field value types must match between cloud and local."""
    if not cloud_response or not local_response:
        pytest.skip("One or both endpoints unreachable")

    local_trips = local_response["data"]["trips"]
    cloud_trips = cloud_response["data"]["trips"]

    if not local_trips or not cloud_trips:
        pytest.skip("No trips from one or both endpoints")

    local_trip = local_trips[0]
    cloud_trip = cloud_trips[0]

    # Check shared keys have matching types
    shared_keys = set(local_trip.keys()) & set(cloud_trip.keys())
    mismatches = {}
    for key in shared_keys:
        lt = type(local_trip[key]).__name__
        ct = type(cloud_trip[key]).__name__
        # Allow None vs str or None vs int
        if lt != ct and local_trip[key] is not None and cloud_trip[key] is not None:
            mismatches[key] = f"local={lt}, cloud={ct}"

    assert not mismatches, f"Type mismatches: {mismatches}"



def test_trips_sorted_by_arrival(local_response):
    """Local proxy trips must be sorted by arrivalTime."""
    if not local_response:
        pytest.skip("Local proxy unreachable")

    trips = local_response["data"]["trips"]
    if len(trips) < 2:
        pytest.skip("Need at least 2 trips to verify sort order")

    times = [t["arrivalTime"] for t in trips]
    assert times == sorted(times), f"Trips not sorted: {times}"



def test_arrival_times_are_plausible(local_response):
    """Local proxy arrival times should be within a reasonable window."""
    if not local_response:
        pytest.skip("Local proxy unreachable")

    trips = local_response["data"]["trips"]
    if not trips:
        pytest.skip("No trips")

    now = int(time.time())
    for t in trips:
        arr = t["arrivalTime"]
        diff_min = (arr - now) / 60
        # Trips should be between -2min and +120min from now
        assert -2 <= diff_min <= 120, (
            f"Trip {t.get('routeName')} has implausible arrivalTime: {diff_min:.0f}m from now"
        )



def test_shared_trips_have_same_route_data(local_response, cloud_response):
    """Trips for the same tripId should have matching route metadata."""
    if not cloud_response or not local_response:
        pytest.skip("One or both endpoints unreachable")

    local_trips = {t["tripId"]: t for t in local_response["data"]["trips"]}
    cloud_trips = {t["tripId"]: t for t in cloud_response["data"]["trips"]}

    shared = set(local_trips.keys()) & set(cloud_trips.keys())
    if not shared:
        pytest.skip("No overlapping tripIds between cloud and local (timing difference)")

    for trip_id in shared:
        lt = local_trips[trip_id]
        ct = cloud_trips[trip_id]
        assert lt["routeId"] == ct["routeId"], f"routeId mismatch for {trip_id}"
        assert lt["routeName"] == ct["routeName"], f"routeName mismatch for {trip_id}"
        # arrivalTime may differ slightly due to offset handling or timing
        diff = abs(lt["arrivalTime"] - ct["arrivalTime"])
        assert diff <= 60, f"arrivalTime differs by {diff}s for {trip_id}"
