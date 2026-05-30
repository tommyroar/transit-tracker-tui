"""Per-stop tile rendering for REST consumers (Home Assistant, dashboards).

The trip-processing pipeline used to live inside ``BaseSimulator._process_trip``,
locked behind an abstract class whose only entrypoint is ``async def run()``.
This module extracts it so the same logic can be reused by REST endpoints
that need processed departures without a Rich console or a WebSocket loop.

Both ``BaseSimulator._process_trip`` (LED simulators) and ``TileCache``
(the Home Assistant tile endpoints) call ``process_trip`` here.
"""

from datetime import datetime
from typing import Optional

from .config import TransitStop, TransitSubscription
from .network.websocket_server import WSF_VESSELS


def _normalize_id(item_id: str) -> str:
    """Strip internal feed prefix and handle WSF special cases.

    Mirrors ``BaseSimulator.normalize_id``; kept here so this module has no
    dependency on simulator.py.
    """
    if not item_id:
        return ""
    s_id = str(item_id)
    if s_id.startswith("wsf:"):
        return s_id.replace("wsf:", "95_")
    if ":" in s_id and "_" in s_id:
        c_idx = s_id.find(":")
        u_idx = s_id.find("_")
        if c_idx < u_idx:
            return s_id[c_idx + 1 :]
    return s_id


def process_trip(
    trip: dict,
    sub: TransitSubscription,
    current_time_ms: int,
    time_display: str = "arrival",
) -> Optional[dict]:
    """Convert a raw OBA trip dict into a display-ready departure dict.

    Returns None if the trip should be filtered (no tripId, no usable time,
    in the past, etc.). The output shape matches what the LED simulators
    consume so they can share this function.

    ``sub`` is the matching subscription (route + stop + label + offset)
    pre-resolved by the caller. Callers must check that the trip's
    normalised route/stop pair matches ``sub`` before calling.
    """
    trip_id = trip.get("tripId")
    if not trip_id:
        return None

    arr_val = (
        trip.get("arrivalTime")
        or trip.get("predictedArrivalTime")
        or trip.get("scheduledArrivalTime")
    )
    dep_val = (
        trip.get("departureTime")
        or trip.get("predictedDepartureTime")
        or trip.get("scheduledDepartureTime")
        or arr_val
    )

    if arr_val is None:
        return None

    now_minus_buffer_ms = current_time_ms - (3600 * 1000)

    if time_display == "departure":
        base_val = (
            dep_val if (dep_val and dep_val > now_minus_buffer_ms / 1000) else arr_val
        )
    else:
        base_val = (
            arr_val if (arr_val and arr_val > now_minus_buffer_ms / 1000) else dep_val
        )

    if base_val is None:
        return None

    if isinstance(base_val, str):
        try:
            dt = datetime.fromisoformat(base_val.replace("Z", "+00:00"))
            base_time_ms = int(dt.timestamp() * 1000)
        except ValueError:
            return None
    elif base_val > 10**12:
        base_time_ms = base_val
    else:
        base_time_ms = base_val * 1000

    if base_time_ms < now_minus_buffer_ms:
        return None

    raw_diff_sec = (base_time_ms - current_time_ms) / 1000.0
    display_mins = int(raw_diff_sec / 60)

    if display_mins < -1:
        return None
    if display_mins < 0:
        display_mins = 0

    route_name = str(trip.get("routeName") or trip.get("routeShortName") or "")
    if not route_name:
        route_name = (
            sub.label.split("-")[0].strip().split()[0]
            if sub.label
            else sub.route.split("_")[-1]
        )

    headsign = trip.get("headsign")

    vehicle_id_full = trip.get("vehicleId")
    if vehicle_id_full and ("95_" in vehicle_id_full or "wsf" in route_name.lower()):
        vehicle_id_short = vehicle_id_full.split("_")[-1]
        vessel_name = WSF_VESSELS.get(vehicle_id_short)
        if vessel_name:
            headsign = vessel_name

    if not headsign and sub.label:
        headsign = sub.label.split("-")[-1].strip() if "-" in sub.label else sub.label

    is_live = trip.get("isRealtime", False)

    color_hex = trip.get("routeColor")
    if "14" in route_name:
        color = "hot_pink"
    elif color_hex:
        color = f"#{color_hex}"
    else:
        color = "yellow"

    return {
        "trip_id": trip_id,
        "diff": display_mins,
        "route": route_name,
        "headsign": headsign or "Transit",
        "color": color,
        "live": is_live,
        "stop_id": _normalize_id(trip.get("stopId", "")),
        "base_time_ms": base_time_ms,
    }


def build_stop_tile(
    stop: TransitStop,
    subs: list[TransitSubscription],
    trips: list[dict],
    current_time_ms: int,
    time_display: str = "arrival",
    limit: int = 5,
) -> dict:
    """Build a per-stop tile suitable for REST consumers (Home Assistant).

    ``trips`` is the raw broadcast list cached by ``TileCache``. ``subs`` is
    the subset of configured subscriptions whose ``stop`` normalises to this
    stop_id (i.e. all route subscriptions on this stop). The function
    processes each trip against the matching sub, sorts by ETA, and trims
    to ``limit`` entries.
    """
    target_stop = _normalize_id(stop.stop_id)
    sub_by_route = {_normalize_id(s.route): s for s in subs}

    departures: list[dict] = []
    seen_trip_ids: set[str] = set()

    for trip in trips:
        if _normalize_id(trip.get("stopId", "")) != target_stop:
            continue
        route_id = _normalize_id(trip.get("routeId", ""))
        sub = sub_by_route.get(route_id)
        if sub is None:
            continue
        dep = process_trip(trip, sub, current_time_ms, time_display)
        if dep is None:
            continue
        if dep["trip_id"] in seen_trip_ids:
            continue
        seen_trip_ids.add(dep["trip_id"])
        departures.append(dep)

    departures.sort(key=lambda d: d["diff"])
    departures = departures[:limit]

    return {
        "stop_id": stop.stop_id,
        "label": stop.label or stop.stop_id,
        "direction": stop.direction,
        "updated": current_time_ms // 1000,
        "departures": [
            {
                "route": d["route"],
                "headsign": d["headsign"],
                "eta_minutes": d["diff"],
                "arrival_time": d["base_time_ms"] // 1000,
                "is_realtime": d["live"],
                "color": d["color"],
                "trip_id": d["trip_id"],
            }
            for d in departures
        ],
    }
