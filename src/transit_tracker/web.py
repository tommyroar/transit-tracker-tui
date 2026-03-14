import asyncio
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional

from .config import TransitConfig
from .transit_api import TransitAPI


def get_mapbox_token(config: TransitConfig) -> str:
    """Resolve Mapbox access token from environment or config."""
    token = os.environ.get("MAPBOX_ACCESS_TOKEN")
    if token:
        return token
    token = config.transit_tracker.mapbox_access_token
    if token:
        return token
    raise RuntimeError(
        "Mapbox access token not found. Set MAPBOX_ACCESS_TOKEN env var "
        "or add mapbox_access_token to transit_tracker config."
    )


async def resolve_stop_coordinates(config: TransitConfig) -> List[Dict[str, Any]]:
    """Fetch lat/lon for all configured stops from the OBA API."""
    api = TransitAPI()
    try:
        tasks = [api.get_stop(stop.stop_id) for stop in config.transit_tracker.stops]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        stops = []
        for stop_cfg, result in zip(config.transit_tracker.stops, results):
            if isinstance(result, Exception):
                print(f"[WEB] Warning: could not fetch stop {stop_cfg.stop_id}: {result}")
                continue
            if result is None:
                print(f"[WEB] Warning: stop {stop_cfg.stop_id} not found")
                continue
            stops.append({
                "stop_id": stop_cfg.stop_id,
                "name": result["name"],
                "lat": result["lat"],
                "lon": result["lon"],
                "label": stop_cfg.label or result["name"],
            })
        return stops
    finally:
        await api.close()


def generate_walkshed_html(stops: List[Dict[str, Any]], mapbox_token: str) -> str:
    """Generate a self-contained HTML page with Mapbox walkshed map."""
    stops_json = json.dumps(stops)
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit Tracker — Walksheds</title>
<script src="https://api.mapbox.com/mapbox-gl-js/v3.12.0/mapbox-gl.js"></script>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.12.0/mapbox-gl.css" rel="stylesheet">
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
  #map {{ position: absolute; top: 0; bottom: 0; width: 100%; }}
  .legend {{
    position: absolute; bottom: 30px; right: 10px;
    background: rgba(20, 20, 30, 0.85); color: #e4e7eb;
    padding: 12px 16px; border-radius: 8px;
    font-size: 13px; line-height: 1.6;
    box-shadow: 0 2px 8px rgba(0,0,0,0.3);
  }}
  .legend h4 {{ margin-bottom: 6px; font-size: 14px; color: #fff; }}
  .legend-item {{ display: flex; align-items: center; gap: 8px; }}
  .legend-swatch {{
    width: 18px; height: 12px; border-radius: 2px;
    border: 1px solid rgba(245, 130, 32, 0.8);
  }}
  .stop-label {{
    background: rgba(20, 20, 30, 0.8); color: #fff;
    padding: 3px 8px; border-radius: 4px;
    font-size: 12px; font-weight: 500;
    white-space: nowrap; pointer-events: none;
  }}
  .loading {{
    position: absolute; top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    background: rgba(20, 20, 30, 0.9); color: #fff;
    padding: 20px 30px; border-radius: 8px;
    font-size: 16px; z-index: 10;
  }}
</style>
</head>
<body>
<div id="map"></div>
<div class="legend">
  <h4>Walk Time</h4>
  <div class="legend-item">
    <div class="legend-swatch" style="background: rgba(245,130,32,0.4);"></div>
    <span>5 minutes</span>
  </div>
  <div class="legend-item">
    <div class="legend-swatch" style="background: rgba(245,130,32,0.25);"></div>
    <span>10 minutes</span>
  </div>
  <div class="legend-item">
    <div class="legend-swatch" style="background: rgba(245,130,32,0.12);"></div>
    <span>15 minutes</span>
  </div>
</div>
<div id="loading" class="loading">Loading walksheds...</div>

<script>
mapboxgl.accessToken = {json.dumps(mapbox_token)};
const STOPS = {stops_json};

const map = new mapboxgl.Map({{
  container: 'map',
  style: 'mapbox://styles/mapbox/light-v11',
  center: STOPS.length ? [STOPS[0].lon, STOPS[0].lat] : [-122.33, 47.60],
  zoom: 14,
  pitchWithRotate: false,
  dragRotate: false
}});

map.addControl(new mapboxgl.NavigationControl(), 'top-left');

map.on('load', async () => {{
  // Fit bounds to all stops
  if (STOPS.length > 1) {{
    const bounds = new mapboxgl.LngLatBounds();
    STOPS.forEach(s => bounds.extend([s.lon, s.lat]));
    map.fitBounds(bounds, {{ padding: 80 }});
  }}

  // Contour config: minutes -> opacity (rendered back-to-front)
  const contours = [
    {{ minutes: 15, fillOpacity: 0.12, label: '15 min' }},
    {{ minutes: 10, fillOpacity: 0.25, label: '10 min' }},
    {{ minutes: 5,  fillOpacity: 0.4,  label: '5 min' }},
  ];

  let loaded = 0;
  for (let i = 0; i < STOPS.length; i++) {{
    const stop = STOPS[i];

    // Stagger requests to avoid rate limiting
    if (i > 0) await new Promise(r => setTimeout(r, 250));

    try {{
      const url = `https://api.mapbox.com/isochrone/v1/mapbox/walking/${{stop.lon}},${{stop.lat}}`
        + `?contours_minutes=5,10,15&polygons=true&access_token=${{mapboxgl.accessToken}}`;
      const resp = await fetch(url);
      if (!resp.ok) {{
        console.warn(`Isochrone failed for ${{stop.name}}:`, resp.status);
        continue;
      }}
      const data = await resp.json();

      // Each feature in data.features corresponds to a contour (15, 10, 5 min)
      data.features.forEach((feature, fi) => {{
        const contour = contours[fi];
        if (!contour) return;
        const id = `walkshed-${{i}}-${{contour.minutes}}`;

        map.addSource(id, {{ type: 'geojson', data: feature }});
        map.addLayer({{
          id: id + '-fill',
          type: 'fill',
          source: id,
          paint: {{
            'fill-color': '#f58220',
            'fill-opacity': contour.fillOpacity,
          }}
        }});
        map.addLayer({{
          id: id + '-outline',
          type: 'line',
          source: id,
          paint: {{
            'line-color': '#f58220',
            'line-width': 1.5,
            'line-opacity': 0.6,
          }}
        }});
      }});

      // Add stop marker
      const el = document.createElement('div');
      el.innerHTML = `<div class="stop-label">${{stop.label}}</div>`;

      new mapboxgl.Marker({{ color: '#f58220' }})
        .setLngLat([stop.lon, stop.lat])
        .setPopup(new mapboxgl.Popup().setHTML(
          `<strong>${{stop.name}}</strong><br>ID: ${{stop.stop_id}}`
        ))
        .addTo(map);

      new mapboxgl.Marker({{ element: el, anchor: 'top' }})
        .setLngLat([stop.lon, stop.lat])
        .addTo(map);

    }} catch (err) {{
      console.error(`Error loading walkshed for ${{stop.name}}:`, err);
    }}
    loaded++;
    document.getElementById('loading').textContent = `Loading walksheds... (${{loaded}}/${{STOPS.length}})`;
  }}
  document.getElementById('loading').style.display = 'none';
}});
</script>
</body>
</html>"""


def generate_index_html(pages: List[Dict[str, str]]) -> str:
    """Generate an index page listing available web pages."""
    links = "".join(
        f'<li><a href="{p["path"]}">{p["name"]}</a> — {p["description"]}</li>'
        for p in pages
    )
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Transit Tracker</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         max-width: 600px; margin: 60px auto; padding: 0 20px; color: #1a202c; }}
  h1 {{ margin-bottom: 8px; }}
  p {{ color: #666; margin-bottom: 24px; }}
  ul {{ list-style: none; padding: 0; }}
  li {{ margin-bottom: 12px; }}
  a {{ color: #f58220; text-decoration: none; font-weight: 600; font-size: 18px; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
  <h1>Transit Tracker</h1>
  <p>Available pages:</p>
  <ul>{links}</ul>
</body>
</html>"""


class TransitWebHandler(BaseHTTPRequestHandler):
    """HTTP request handler with a routes dict for extensibility."""

    routes: Dict[str, str] = {}

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        content = self.routes.get(path)
        if content is not None:
            content_type = "application/json" if path.startswith("/api/") else "text/html"
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>404 Not Found</h1>")

    def log_message(self, format, *args):
        print(f"[WEB] {args[0]}")


async def run_web(config: TransitConfig, host: str = "0.0.0.0", port: int = 8080):
    """Start the Transit Tracker web server."""
    mapbox_token = get_mapbox_token(config)

    print(f"[WEB] Resolving stop coordinates...")
    stops = await resolve_stop_coordinates(config)
    if not stops:
        print("[WEB] No stops found in config. Add stops first with 'transit-tracker'.")
        return

    print(f"[WEB] Resolved {len(stops)} stops")

    # Build routes
    walkshed_html = generate_walkshed_html(stops, mapbox_token)
    stops_json = json.dumps(stops, indent=2)

    pages = [
        {"path": "/walkshed", "name": "Walksheds", "description": "Walking distance isochrone map"},
    ]
    index_html = generate_index_html(pages)

    TransitWebHandler.routes = {
        "/": index_html,
        "/walkshed": walkshed_html,
        "/api/stops": stops_json,
    }

    server = HTTPServer((host, port), TransitWebHandler)
    print(f"[WEB] Transit Tracker web server at http://{host}:{port}")
    print(f"[WEB]   /walkshed  — Walking distance map")
    print(f"[WEB]   /api/stops — Stop coordinates JSON")
    print(f"[WEB] Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[WEB] Shutting down...")
        server.shutdown()
