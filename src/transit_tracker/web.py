import asyncio
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List

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
        for stop_cfg, result in zip(config.transit_tracker.stops, results, strict=True):
            if isinstance(result, Exception):
                print(
                    f"[WEB] Warning: could not fetch stop {stop_cfg.stop_id}: {result}"
                )
                continue
            if result is None:
                print(f"[WEB] Warning: stop {stop_cfg.stop_id} not found")
                continue
            stops.append(
                {
                    "stop_id": stop_cfg.stop_id,
                    "name": result["name"],
                    "lat": result["lat"],
                    "lon": result["lon"],
                    "label": stop_cfg.label or result["name"],
                }
            )
        return stops
    finally:
        await api.close()


async def resolve_route_polylines(config: TransitConfig) -> List[Dict[str, Any]]:
    """Fetch route shape polylines for all configured routes."""
    api = TransitAPI()
    try:
        # Collect unique route IDs
        route_ids = set()
        for stop in config.transit_tracker.stops:
            for route_id in stop.routes:
                route_ids.add(route_id)

        if not route_ids:
            return []

        tasks = [api.get_route_polylines(rid) for rid in route_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        routes = []
        for result in results:
            if isinstance(result, Exception):
                print(f"[WEB] Warning: could not fetch route polylines: {result}")
                continue
            if result and result.get("polylines"):
                routes.append(result)
        return routes
    finally:
        await api.close()


# --- Shared Mapbox styling constants (Issue #16) ---

MAPBOX_STYLE_JS = """
// Architectural map style overrides (Issue #16)
const MAP_COLORS = {
  background: '#cbd2d9',
  land: '#e4e7eb',
  roads: '#ffffff',
  buildings: '#d1d5db',
  buildingOutline: '#9ca3af',
  highlight: '#f58220',
  text: '#1a202c',
};

function applyArchitecturalStyle(map) {
  // Style buildings
  if (map.getLayer('building')) {
    map.setPaintProperty('building', 'fill-color', MAP_COLORS.buildings);
    map.setPaintProperty('building', 'fill-outline-color', MAP_COLORS.buildingOutline);
  }
  // Style roads white
  ['road-street', 'road-primary', 'road-secondary', 'road-tertiary',
   'road-minor-low', 'road-minor-case'].forEach(layer => {
    if (map.getLayer(layer)) {
      try { map.setPaintProperty(layer, 'line-color', MAP_COLORS.roads); } catch(e) {}
    }
  });
}
"""

STOP_MARKER_CSS = """
  .stop-pill {
    background: #f58220; color: #fff;
    padding: 4px 10px; border-radius: 4px;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-weight: 600; font-size: 12px;
    white-space: nowrap; pointer-events: none;
    box-shadow: 0 2px 4px rgba(0,0,0,0.2);
  }
  .stop-pill::after {
    content: ''; position: absolute;
    bottom: -5px; left: 50%; transform: translateX(-50%);
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 5px solid #f58220;
  }
"""


def generate_walkshed_html(
    stops: List[Dict[str, Any]],
    mapbox_token: str,
    routes: List[Dict[str, Any]] = None,
) -> str:
    """Generate a self-contained HTML page with Mapbox walkshed map."""
    stops_json = json.dumps(stops)
    routes_json = json.dumps(routes or [])
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
  .legend-swatch.route {{
    border: none; height: 3px; border-radius: 1px;
  }}
  {STOP_MARKER_CSS}
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
  <div id="route-legend"></div>
</div>
<div id="loading" class="loading">Loading walksheds...</div>

<script>
{MAPBOX_STYLE_JS}

mapboxgl.accessToken = {json.dumps(mapbox_token)};
const STOPS = {stops_json};
const ROUTES = {routes_json};

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
  applyArchitecturalStyle(map);

  // Fit bounds to all stops
  if (STOPS.length > 1) {{
    const bounds = new mapboxgl.LngLatBounds();
    STOPS.forEach(s => bounds.extend([s.lon, s.lat]));
    map.fitBounds(bounds, {{ padding: 80 }});
  }}

  // Add route polylines (Issue #14)
  const legendEl = document.getElementById('route-legend');
  ROUTES.forEach((route, ri) => {{
    const color = route.color ? `#${{route.color}}` : '#666';
    route.polylines.forEach((coords, pi) => {{
      const id = `route-${{ri}}-${{pi}}`;
      map.addSource(id, {{
        type: 'geojson',
        data: {{ type: 'Feature', geometry: {{ type: 'LineString', coordinates: coords }} }}
      }});
      map.addLayer({{
        id: id,
        type: 'line',
        source: id,
        paint: {{
          'line-color': color,
          'line-width': 3,
          'line-opacity': 0.7,
        }}
      }});
    }});
    if (route.name) {{
      legendEl.innerHTML += `<div class="legend-item" style="margin-top:4px">` +
        `<div class="legend-swatch route" style="background:${{color}}"></div>` +
        `<span>Route ${{route.name}}</span></div>`;
    }}
  }});

  // Contour config: minutes -> opacity (rendered back-to-front)
  const contours = [
    {{ minutes: 15, fillOpacity: 0.12 }},
    {{ minutes: 10, fillOpacity: 0.25 }},
    {{ minutes: 5,  fillOpacity: 0.4 }},
  ];

  let loaded = 0;
  for (let i = 0; i < STOPS.length; i++) {{
    const stop = STOPS[i];
    if (i > 0) await new Promise(r => setTimeout(r, 250));

    try {{
      const url = `https://api.mapbox.com/isochrone/v1/mapbox/walking/${{stop.lon}},${{stop.lat}}`
        + `?contours_minutes=5,10,15&polygons=true&access_token=${{mapboxgl.accessToken}}`;
      const resp = await fetch(url);
      if (!resp.ok) {{ console.warn(`Isochrone failed for ${{stop.name}}:`, resp.status); continue; }}
      const data = await resp.json();

      data.features.forEach((feature, fi) => {{
        const contour = contours[fi];
        if (!contour) return;
        const id = `walkshed-${{i}}-${{contour.minutes}}`;
        map.addSource(id, {{ type: 'geojson', data: feature }});
        map.addLayer({{
          id: id + '-fill', type: 'fill', source: id,
          paint: {{ 'fill-color': MAP_COLORS.highlight, 'fill-opacity': contour.fillOpacity }}
        }});
        map.addLayer({{
          id: id + '-outline', type: 'line', source: id,
          paint: {{ 'line-color': MAP_COLORS.highlight, 'line-width': 1.5, 'line-opacity': 0.6 }}
        }});
      }});

      // Stop marker with pill label
      new mapboxgl.Marker({{ color: MAP_COLORS.highlight }})
        .setLngLat([stop.lon, stop.lat])
        .setPopup(new mapboxgl.Popup().setHTML(
          `<strong>${{stop.name}}</strong><br>ID: ${{stop.stop_id}}`
        ))
        .addTo(map);

      const el = document.createElement('div');
      el.innerHTML = `<div class="stop-pill">${{stop.label}}</div>`;
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


def generate_simulator_html(config: TransitConfig) -> str:
    """Generate a self-contained web LED matrix simulator page (Issue #15)."""
    # Build subscription pairs string (same logic as simulator.py lines 197-215)
    pairs = []
    for sub in config.subscriptions:
        r_id = sub.route if ":" in sub.route else f"{sub.feed}:{sub.route}"
        s_id = sub.stop if ":" in sub.stop else f"{sub.feed}:{sub.stop}"
        off_sec = 0
        match = re.search(r"(-?\d+)", str(sub.time_offset))
        if match:
            off_sec = int(match.group(1)) * 60
        pairs.append(f"{r_id},{s_id},{off_sec}")
    pairs_str = ";".join(pairs)

    api_url = config.api_url
    if (
        config.use_local_api
        and "localhost" not in api_url
        and "127.0.0.1" not in api_url
    ):
        api_url = "ws://localhost:8000"

    display_width = config.panel_width * config.num_panels
    display_height = 32
    pixel_scale = 7

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit Tracker — LED Simulator</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    background: #111; color: #e4e7eb;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    display: flex; flex-direction: column; align-items: center;
    justify-content: center; min-height: 100vh; gap: 16px;
  }}
  canvas {{
    border-radius: 8px;
    box-shadow: 0 0 40px rgba(0,0,0,0.5);
  }}
  .status {{
    font-size: 13px; color: #888;
    display: flex; gap: 12px; align-items: center;
  }}
  .status .dot {{
    width: 8px; height: 8px; border-radius: 50%;
    display: inline-block; margin-right: 4px;
  }}
  .status .dot.connected {{ background: #4ade80; }}
  .status .dot.disconnected {{ background: #ef4444; }}
  .status .dot.connecting {{ background: #facc15; }}
  h1 {{
    font-size: 16px; font-weight: 600; color: #999;
    letter-spacing: 1px; text-transform: uppercase;
  }}
</style>
</head>
<body>
<h1>HUB75 {display_width}x{display_height} LED Simulator</h1>
<canvas id="led" width="{display_width * pixel_scale}" height="{display_height * pixel_scale}"></canvas>
<div class="status">
  <span><span id="statusDot" class="dot connecting"></span><span id="statusText">Connecting...</span></span>
  <span id="stopInfo"></span>
</div>

<script>
const CONFIG = {{
  apiUrl: {json.dumps(api_url)},
  pairsStr: {json.dumps(pairs_str)},
  displayWidth: {display_width},
  displayHeight: {display_height},
  pixelScale: {pixel_scale},
}};

// --- MicroFont (ported from simulator.py) ---
const GLYPHS = {{
  '0':[0x0E,0x11,0x11,0x11,0x11,0x11,0x0E],
  '1':[0x04,0x0C,0x04,0x04,0x04,0x04,0x0E],
  '2':[0x0E,0x11,0x01,0x02,0x04,0x08,0x1F],
  '3':[0x1F,0x02,0x04,0x02,0x01,0x11,0x0E],
  '4':[0x02,0x06,0x0A,0x12,0x1F,0x02,0x02],
  '5':[0x1F,0x10,0x1E,0x01,0x01,0x11,0x0E],
  '6':[0x0E,0x10,0x10,0x1E,0x11,0x11,0x0E],
  '7':[0x1F,0x01,0x02,0x04,0x08,0x08,0x08],
  '8':[0x0E,0x11,0x11,0x0E,0x11,0x11,0x0E],
  '9':[0x0E,0x11,0x11,0x0F,0x01,0x02,0x0C],
  'A':[0x04,0x0A,0x11,0x11,0x1F,0x11,0x11],
  'B':[0x1E,0x11,0x11,0x1E,0x11,0x11,0x1E],
  'C':[0x0E,0x11,0x10,0x10,0x10,0x11,0x0E],
  'D':[0x1C,0x12,0x11,0x11,0x11,0x12,0x1C],
  'E':[0x1F,0x10,0x10,0x1E,0x10,0x10,0x1F],
  'F':[0x1F,0x10,0x10,0x1E,0x10,0x10,0x10],
  'G':[0x0E,0x11,0x10,0x17,0x11,0x11,0x0F],
  'H':[0x11,0x11,0x11,0x1F,0x11,0x11,0x11],
  'I':[0x0E,0x04,0x04,0x04,0x04,0x04,0x0E],
  'J':[0x07,0x02,0x02,0x02,0x02,0x12,0x0C],
  'K':[0x11,0x12,0x14,0x18,0x14,0x12,0x11],
  'L':[0x10,0x10,0x10,0x10,0x10,0x10,0x1F],
  'M':[0x11,0x1B,0x15,0x11,0x11,0x11,0x11],
  'N':[0x11,0x11,0x19,0x15,0x13,0x11,0x11],
  'O':[0x0E,0x11,0x11,0x11,0x11,0x11,0x0E],
  'P':[0x1E,0x11,0x11,0x1E,0x10,0x10,0x10],
  'Q':[0x0E,0x11,0x11,0x11,0x15,0x12,0x0D],
  'R':[0x1E,0x11,0x11,0x1E,0x14,0x12,0x11],
  'S':[0x0E,0x11,0x10,0x0E,0x01,0x11,0x0E],
  'T':[0x1F,0x04,0x04,0x04,0x04,0x04,0x04],
  'U':[0x11,0x11,0x11,0x11,0x11,0x11,0x0E],
  'V':[0x11,0x11,0x11,0x11,0x11,0x0A,0x04],
  'W':[0x11,0x11,0x11,0x15,0x15,0x1B,0x11],
  'X':[0x11,0x11,0x0A,0x04,0x0A,0x11,0x11],
  'Y':[0x11,0x11,0x0A,0x04,0x04,0x04,0x04],
  'Z':[0x1F,0x01,0x02,0x04,0x08,0x10,0x1F],
  ' ':[0x00,0x00,0x00,0x00,0x00,0x00,0x00],
  'm':[0x00,0x00,0x1A,0x15,0x15,0x15,0x15],
  '.':[0x00,0x00,0x00,0x00,0x00,0x00,0x04],
  '-':[0x00,0x00,0x00,0x1F,0x00,0x00,0x00],
  '>':[0x10,0x08,0x04,0x08,0x10,0x00,0x00],
  '(':[0x04,0x08,0x08,0x08,0x08,0x08,0x04],
  ')':[0x08,0x04,0x04,0x04,0x04,0x04,0x08],
  '/':[0x01,0x02,0x04,0x08,0x10,0x00,0x00],
  '?':[0x0E,0x11,0x01,0x02,0x04,0x00,0x04],
}};

const REALTIME_ICON = [
  [0,0,0,3,3,3],
  [0,0,3,0,0,0],
  [0,3,0,0,2,2],
  [3,0,0,2,0,0],
  [3,0,2,0,0,1],
  [3,0,2,0,1,1],
];

function getBitmap(text) {{
  const rows = [[], [], [], [], [], [], []];
  for (const ch of text) {{
    const glyph = GLYPHS[ch.toUpperCase()] || GLYPHS['?'];
    for (let r = 0; r < 7; r++) {{
      for (let b = 4; b >= 0; b--) {{
        rows[r].push((glyph[r] >> b) & 1);
      }}
      rows[r].push(0); // gap
    }}
  }}
  return rows;
}}

function getLiveIconFrame(elapsedMs) {{
  const cycleMs = elapsedMs % 4000;
  let frame = 0;
  if (cycleMs >= 3000) {{
    frame = Math.min(Math.floor((cycleMs - 3000) / 200) + 1, 5);
  }}
  const rows = [[], [], [], [], [], [], []];
  for (let r = 0; r < 6; r++) {{
    for (let c = 0; c < 6; c++) {{
      const seg = REALTIME_ICON[r][c];
      if (seg === 0) {{ rows[r].push(0); continue; }}
      let lit = false;
      if (seg === 1 && [1,2,3].includes(frame)) lit = true;
      else if (seg === 2 && [2,3,4].includes(frame)) lit = true;
      else if (seg === 3 && [3,4,5].includes(frame)) lit = true;
      rows[r].push(lit ? 2 : 1);
    }}
  }}
  rows[6] = [0,0,0,0,0,0];
  return rows;
}}

// --- Color mapping ---
const COLORS = {{
  yellow: '#FFD700',
  hot_pink: '#FF69B4',
  white: '#FFFFFF',
  bright_blue: '#5B9BD5',
  grey74: '#BDBDBD',
  cyan: '#00CED1',
}};

function resolveColor(c) {{
  if (!c) return COLORS.yellow;
  if (c.startsWith('#')) return c;
  return COLORS[c] || COLORS.yellow;
}}

// --- State ---
let trips = [];
let startTime = performance.now();
let wsStatus = 'connecting';

// --- WebSocket ---
function connectWS() {{
  wsStatus = 'connecting';
  updateStatus();
  const ws = new WebSocket(CONFIG.apiUrl);

  ws.onopen = () => {{
    wsStatus = 'connected';
    updateStatus();
    ws.send(JSON.stringify({{
      event: 'schedule:subscribe',
      client_name: 'Web Simulator',
      data: {{ routeStopPairs: CONFIG.pairsStr, limit: 10 }}
    }}));
  }};

  ws.onmessage = (evt) => {{
    const data = JSON.parse(evt.data);
    if (data.event === 'schedule') {{
      const payload = data.payload || data.data || {{}};
      trips = payload.trips || [];
    }}
  }};

  ws.onclose = () => {{
    wsStatus = 'disconnected';
    updateStatus();
    setTimeout(connectWS, 5000);
  }};

  ws.onerror = () => {{ ws.close(); }};
}}

function updateStatus() {{
  const dot = document.getElementById('statusDot');
  const text = document.getElementById('statusText');
  dot.className = 'dot ' + wsStatus;
  text.textContent = wsStatus.charAt(0).toUpperCase() + wsStatus.slice(1);
}}

// --- Departure processing (port of get_upcoming_departures) ---
function getUpcomingDepartures() {{
  const nowMs = Date.now();
  const deps = [];

  for (const trip of trips) {{
    let arrVal = trip.arrivalTime || trip.predictedArrivalTime || trip.scheduledArrivalTime;
    if (!arrVal) continue;
    // Convert to ms if in seconds
    if (arrVal < 1e12) arrVal *= 1000;

    const diffMin = Math.floor((arrVal - nowMs) / 60000);
    if (diffMin < -1) continue;

    const routeName = trip.routeName || trip.routeShortName || '';
    const headsign = trip.headsign || trip.tripHeadsign || 'Transit';
    const isLive = !!trip.isRealtime;
    const colorHex = trip.routeColor;

    let color = 'yellow';
    if (routeName.includes('14')) color = 'hot_pink';
    else if (colorHex) color = '#' + colorHex;

    deps.push({{
      tripId: trip.tripId,
      diff: Math.max(0, diffMin),
      route: routeName,
      headsign: headsign,
      color: color,
      live: isLive,
      stopId: trip.stopId,
    }});
  }}

  deps.sort((a, b) => a.diff - b.diff);

  // Fair diversity capping
  const limit = 3;
  const final = [];
  const seenStops = new Set();

  // Pass 1: one per stop
  for (const d of deps) {{
    if (!seenStops.has(d.stopId)) {{
      final.push(d);
      seenStops.add(d.stopId);
    }}
    if (final.length >= limit) break;
  }}
  // Pass 2: fill remaining
  if (final.length < limit) {{
    for (const d of deps) {{
      if (!final.includes(d)) final.push(d);
      if (final.length >= limit) break;
    }}
  }}

  final.sort((a, b) => a.diff - b.diff);
  return final;
}}

// --- Canvas rendering ---
const canvas = document.getElementById('led');
const ctx = canvas.getContext('2d');
const W = CONFIG.displayWidth;
const H = CONFIG.displayHeight;
const S = CONFIG.pixelScale;
const LED_RADIUS = S * 0.35;
const DIM_COLOR = '#1a1a2e';

function renderFrame() {{
  const elapsed = performance.now() - startTime;
  ctx.fillStyle = '#0a0a0f';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Build pixel canvas
  const pixels = Array.from({{ length: H }}, () => Array(W).fill(null));
  const departures = getUpcomingDepartures();

  if (departures.length === 0) {{
    // "Connecting..." or "No data" message
    const msg = trips.length === 0 ? 'Connecting...' : 'No Buses';
    const bm = getBitmap(msg);
    for (let r = 0; r < 7 && r < bm.length; r++) {{
      for (let c = 0; c < bm[r].length && c < W; c++) {{
        if (bm[r][c]) pixels[r][c] = COLORS.cyan;
      }}
    }}
  }} else {{
    let yOffset = 0;
    for (let di = 0; di < departures.length && di < 3; di++) {{
      const dep = departures[di];
      renderTripRow(pixels, dep, elapsed, yOffset);
      yOffset += 8; // 7 rows + 1 spacer
    }}
  }}

  // Draw LEDs
  for (let r = 0; r < H; r++) {{
    for (let c = 0; c < W; c++) {{
      const cx = c * S + S / 2;
      const cy = r * S + S / 2;
      ctx.beginPath();
      ctx.arc(cx, cy, LED_RADIUS, 0, Math.PI * 2);
      ctx.fillStyle = pixels[r][c] || DIM_COLOR;
      ctx.fill();
    }}
  }}

  requestAnimationFrame(renderFrame);
}}

function renderTripRow(pixels, dep, elapsedMs, yOff) {{
  const routeBm = getBitmap(dep.route);
  const routeW = routeBm[0].length;
  const timeText = dep.diff <= 0 ? 'Now' : dep.diff + 'm';
  const timeBm = getBitmap(timeText);
  const timeW = timeBm[0].length;
  const isLive = dep.live;
  const iconW = isLive ? 6 : 0;

  // Headsign area
  const hsXStart = routeW + 3;
  const hsAreaW = W - hsXStart - timeW - (isLive ? iconW + 2 : 0);
  const hsBmFull = getBitmap(dep.headsign);
  const hsFullW = hsBmFull[0].length;

  // Scrolling
  let scrollOff = 0;
  if (hsFullW > hsAreaW) {{
    const overflow = hsFullW - hsAreaW;
    const scrollSpeed = 100; // ms per pixel
    const waitMs = 2000;
    const scrollDur = overflow * scrollSpeed;
    const totalCycle = (waitMs + scrollDur) * 2;
    const pos = elapsedMs % totalCycle;
    if (pos < waitMs) scrollOff = 0;
    else if (pos < waitMs + scrollDur) scrollOff = Math.floor((pos - waitMs) / scrollSpeed);
    else if (pos < waitMs * 2 + scrollDur) scrollOff = overflow;
    else scrollOff = overflow - Math.floor((pos - waitMs * 2 - scrollDur) / scrollSpeed);
    scrollOff = Math.max(0, Math.min(overflow, scrollOff));
  }}

  const routeColor = resolveColor(dep.color);
  const timeColor = isLive ? COLORS.bright_blue : COLORS.grey74;

  // Draw route
  for (let r = 0; r < 7; r++) {{
    for (let c = 0; c < routeW && c < W; c++) {{
      if (routeBm[r][c]) pixels[yOff + r][c] = routeColor;
    }}
  }}

  // Draw time (right-aligned)
  const timeX = W - timeW;
  for (let r = 0; r < 7; r++) {{
    for (let c = 0; c < timeW; c++) {{
      const tx = timeX + c;
      if (tx >= 0 && tx < W && timeBm[r][c]) pixels[yOff + r][tx] = timeColor;
    }}
  }}

  // Draw LIVE icon
  if (isLive) {{
    const iconBm = getLiveIconFrame(elapsedMs);
    const iconX = timeX - 8;
    for (let r = 0; r < 6; r++) {{
      for (let c = 0; c < 6; c++) {{
        const ix = iconX + c;
        if (ix >= 0 && ix < W) {{
          const val = iconBm[r][c];
          if (val === 2) pixels[yOff + r][ix] = COLORS.white;
          else if (val === 1) pixels[yOff + r][ix] = COLORS.bright_blue;
        }}
      }}
    }}
  }}

  // Draw headsign (with scrolling + clipping)
  for (let r = 0; r < 7; r++) {{
    for (let c = 0; c < hsAreaW; c++) {{
      const srcC = c + scrollOff;
      const destX = hsXStart + c;
      if (destX < W && srcC < hsFullW && hsBmFull[r][srcC]) {{
        pixels[yOff + r][destX] = COLORS.white;
      }}
    }}
  }}
}}

// --- Init ---
connectWS();
requestAnimationFrame(renderFrame);
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
            content_type = (
                "application/json" if path.startswith("/api/") else "text/html"
            )
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

    print("[WEB] Resolving stop coordinates...")
    stops = await resolve_stop_coordinates(config)
    if not stops:
        print("[WEB] No stops found in config. Add stops first with 'transit-tracker'.")
        return

    print(f"[WEB] Resolved {len(stops)} stops")

    print("[WEB] Fetching route polylines...")
    routes = await resolve_route_polylines(config)
    print(f"[WEB] Fetched {len(routes)} route shapes")

    # Build pages
    walkshed_html = generate_walkshed_html(stops, mapbox_token, routes)
    simulator_html = generate_simulator_html(config)
    stops_json = json.dumps(stops, indent=2)

    pages = [
        {
            "path": "/walkshed",
            "name": "Walksheds",
            "description": "Walking distance isochrone map with route lines",
        },
        {
            "path": "/simulator",
            "name": "LED Simulator",
            "description": "Web-based HUB75 LED matrix simulator",
        },
    ]
    index_html = generate_index_html(pages)

    TransitWebHandler.routes = {
        "/": index_html,
        "/walkshed": walkshed_html,
        "/simulator": simulator_html,
        "/api/stops": stops_json,
    }

    server = HTTPServer((host, port), TransitWebHandler)
    print(f"[WEB] Transit Tracker web server at http://{host}:{port}")
    print("[WEB]   /walkshed   — Walking distance map with route lines")
    print("[WEB]   /simulator  — LED matrix simulator")
    print("[WEB]   /api/stops  — Stop coordinates JSON")
    print("[WEB] Press Ctrl+C to stop")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[WEB] Shutting down...")
        server.shutdown()
