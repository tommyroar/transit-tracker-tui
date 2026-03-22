#!/usr/bin/env python3
"""
Fetch Mapbox isochrone walksheds for all Sound Transit 1 Line + 2 Line stations
and write data/stations.geojson.

Requires a Mapbox access token — set via:
  MAPBOX_TOKEN=pk.xxx uv run python scripts/build_stations_geojson.py
  or add mapbox_access_token to your config.yaml

Usage:
    uv run python scripts/build_stations_geojson.py
    open data/stations.geojson
"""

import csv
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
GTFS_DIR = PROJECT_ROOT / "data" / "gtfs"
OUTPUT = PROJECT_ROOT / "data" / "stations.geojson"

TARGET_ROUTES = {"100479", "2LINE"}  # 1 Line, 2 Line only
CONTOUR_MINUTES = 10


def get_token() -> str:
    token = os.environ.get("MAPBOX_TOKEN")
    if token:
        return token

    # Try config.yaml
    try:
        import yaml
        config_path = PROJECT_ROOT / "config.yaml"
        if not config_path.exists():
            config_path = PROJECT_ROOT / ".local" / "config.yaml"
        if config_path.exists():
            data = yaml.safe_load(config_path.read_text()) or {}
            token = (data.get("transit_tracker") or {}).get("mapbox_access_token")
            if token:
                return token
    except Exception:
        pass

    print("ERROR: No Mapbox token found.", file=sys.stderr)
    print("Set MAPBOX_TOKEN env var or add mapbox_access_token to config.yaml", file=sys.stderr)
    sys.exit(1)


def load_stations() -> list[dict]:
    """Return deduplicated 1 Line + 2 Line stations from GTFS."""
    trips_path = GTFS_DIR / "40" / "trips.txt"
    stop_times_path = GTFS_DIR / "40" / "stop_times.txt"
    stops_path = GTFS_DIR / "40" / "stops.txt"

    for p in [trips_path, stop_times_path, stops_path]:
        if not p.exists():
            print(f"ERROR: {p} not found. Run scripts/download_gtfs.py first.", file=sys.stderr)
            sys.exit(1)

    with open(trips_path) as f:
        trip_ids = {t["trip_id"] for t in csv.DictReader(f) if t["route_id"] in TARGET_ROUTES}
    with open(stop_times_path) as f:
        stop_ids = {r["stop_id"] for r in csv.DictReader(f) if r["trip_id"] in trip_ids}

    seen: set[str] = set()
    stations = []
    with open(stops_path) as f:
        for s in csv.DictReader(f):
            if s["stop_id"] in stop_ids and s["stop_name"] not in seen:
                seen.add(s["stop_name"])
                stations.append({
                    "name": s["stop_name"],
                    "lat": float(s["stop_lat"]),
                    "lon": float(s["stop_lon"]),
                })
    return stations


def fetch_isochrone(lat: float, lon: float, token: str) -> dict:
    url = (
        f"https://api.mapbox.com/isochrone/v1/mapbox/walking/{lon},{lat}"
        f"?contours_minutes={CONTOUR_MINUTES}&polygons=true&access_token={token}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "transit-tracker/1.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data["features"][0]["geometry"]


def main() -> None:
    token = get_token()
    stations = load_stations()
    print(f"=== Building stations.geojson ({len(stations)} stations) ===\n")

    features = []
    for i, station in enumerate(stations):
        print(f"  [{i+1}/{len(stations)}] {station['name']}...", end=" ", flush=True)
        walkshed = fetch_isochrone(station["lat"], station["lon"], token)
        print("ok")
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [station["lon"], station["lat"]]},
            "properties": {
                "name": station["name"],
                "walkshed": walkshed,
            },
        })
        if i < len(stations) - 1:
            time.sleep(0.15)  # stay under Mapbox rate limit

    geojson = {"type": "FeatureCollection", "features": features}
    OUTPUT.write_text(json.dumps(geojson, separators=(",", ":")))
    print(f"\nWritten to {OUTPUT}")


if __name__ == "__main__":
    main()
