#!/usr/bin/env python3
"""
Build an interactive HTML route map from GTFS static schedule data.

Reads shapes.txt and routes.txt from data/gtfs/{agency_id}/ and produces
data/route_map.html — a Leaflet.js map with all routes color-coded by agency.

Usage:
    uv run python scripts/build_route_map.py
    open data/route_map.html
"""

import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
GTFS_DIR = PROJECT_ROOT / "data" / "gtfs"
OUTPUT = PROJECT_ROOT / "data" / "route_map.html"

AGENCIES = {
    "1": "King County Metro",
    "40": "Sound Transit",
    "95": "Washington State Ferries",
}

# Fallback colors per agency if route has no color
AGENCY_COLORS = {
    "1": "#FDB71A",
    "40": "#28813F",
    "95": "#005DAA",
}


def load_routes(agency_id: str) -> dict[str, dict]:
    """route_id -> {short_name, long_name, color}"""
    path = GTFS_DIR / agency_id / "routes.txt"
    routes = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            color = row.get("route_color", "").strip()
            routes[row["route_id"]] = {
                "short_name": row.get("route_short_name", "").strip(),
                "long_name": row.get("route_long_name", "").strip(),
                "color": f"#{color}" if color else AGENCY_COLORS[agency_id],
                "agency": AGENCIES[agency_id],
            }
    return routes


def load_shape_ids_per_route(agency_id: str) -> dict[str, str]:
    """route_id -> one representative shape_id (first trip found)"""
    path = GTFS_DIR / agency_id / "trips.txt"
    seen: dict[str, str] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            rid = row["route_id"]
            sid = row.get("shape_id", "").strip()
            if sid and rid not in seen:
                seen[rid] = sid
    return seen


def load_shapes(agency_id: str) -> dict[str, list[list[float]]]:
    """shape_id -> [[lat, lon], ...] sorted by sequence"""
    path = GTFS_DIR / agency_id / "shapes.txt"
    raw: dict[str, list[tuple[int, float, float]]] = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            sid = row["shape_id"]
            seq = int(row["shape_pt_sequence"])
            lat = float(row["shape_pt_lat"])
            lon = float(row["shape_pt_lon"])
            raw.setdefault(sid, []).append((seq, lat, lon))
    return {
        sid: [[lat, lon] for _, lat, lon in sorted(pts)]
        for sid, pts in raw.items()
    }


def build_geojson() -> dict:
    features = []
    for agency_id in AGENCIES:
        print(f"  Loading {AGENCIES[agency_id]}...")
        routes = load_routes(agency_id)
        route_shapes = load_shape_ids_per_route(agency_id)
        shapes = load_shapes(agency_id)

        for route_id, route_info in routes.items():
            shape_id = route_shapes.get(route_id)
            if not shape_id or shape_id not in shapes:
                continue
            coords = shapes[shape_id]
            # GeoJSON uses [lon, lat]
            geojson_coords = [[lon, lat] for lat, lon in coords]
            features.append({
                "type": "Feature",
                "properties": {
                    "route_id": route_id,
                    "short_name": route_info["short_name"],
                    "long_name": route_info["long_name"],
                    "color": route_info["color"],
                    "agency": route_info["agency"],
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": geojson_coords,
                },
            })

    print(f"  Total routes with shapes: {len(features)}")
    return {"type": "FeatureCollection", "features": features}


HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Transit Route Map</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
  body {{ margin: 0; font-family: sans-serif; }}
  #map {{ height: 100vh; }}
  .legend {{ background: white; padding: 10px 14px; border-radius: 6px; line-height: 1.8; font-size: 13px; }}
  .legend-dot {{ display: inline-block; width: 14px; height: 4px; border-radius: 2px; margin-right: 6px; vertical-align: middle; }}
</style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const geojson = {geojson};

const map = L.map('map').setView([47.6062, -122.3321], 11);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/light_all/{{z}}/{{x}}/{{y}}{{r}}.png', {{
  attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  maxZoom: 19,
}}).addTo(map);

const agencyColors = {agency_colors};

// Layer groups per agency for legend toggle
const layers = {{}};
Object.keys(agencyColors).forEach(a => {{ layers[a] = L.layerGroup().addTo(map); }});

L.geoJSON(geojson, {{
  style: f => ({{ color: f.properties.color, weight: 2, opacity: 0.75 }}),
  onEachFeature: (f, layer) => {{
    const p = f.properties;
    layer.bindTooltip(`<b>${{p.short_name || p.route_id}}</b> ${{p.long_name}}<br/><small>${{p.agency}}</small>`);
    if (layers[p.agency]) layers[p.agency].addLayer(layer);
  }}
}});

// Legend
const legend = L.control({{ position: 'bottomright' }});
legend.onAdd = () => {{
  const div = L.DomUtil.create('div', 'legend');
  div.innerHTML = '<b>Agencies</b><br/>' +
    Object.entries(agencyColors).map(([name, color]) =>
      `<span class="legend-dot" style="background:${{color}}"></span>${{name}}<br/>`
    ).join('');
  return div;
}};
legend.addTo(map);
</script>
</body>
</html>
"""


def main() -> None:
    print("=== Building Route Map ===\n")
    geojson = build_geojson()

    agency_colors_js = json.dumps({v: AGENCY_COLORS[k] for k, v in AGENCIES.items()})
    geojson_js = json.dumps(geojson)

    html = HTML_TEMPLATE.format(
        geojson=geojson_js,
        agency_colors=agency_colors_js,
    )

    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(html)
    print(f"\nMap written to: {OUTPUT}")
    print("Open with: open data/route_map.html")


if __name__ == "__main__":
    main()
