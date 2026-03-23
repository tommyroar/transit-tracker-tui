"""HTML page generators for the Transit Tracker web server.

Contains the large inline HTML/CSS/JS templates for the index,
monitor, dashboard, and simulator pages.
"""

import json
import os
from typing import Dict, List


def generate_index_html(pages: List[Dict[str, str]]) -> str:
    """Generate an index page listing available web pages."""
    cards = "".join(
        '<a href="' + p["path"] + '" class="card"><h2>' + p["name"] + '</h2><p>' + p["description"] + '</p></a>'
        for p in pages
    )
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Transit Tracker</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect width='10' height='3' rx='1.5' fill='%23e8a830'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root { --bg:#06080e;--bg1:#0c0f1a;--border:#1a1e35;--text0:#eae8e4;--text2:#5c6080;--amber:#e8a830;--amber-bg:rgba(232,168,48,0.08); }
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:'Outfit',system-ui,sans-serif;background:var(--bg);color:var(--text0);display:flex;flex-direction:column;align-items:center;justify-content:center;min-height:100vh;padding:40px 20px}
body::after{content:'';position:fixed;inset:0;background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E");opacity:0.018;pointer-events:none;z-index:9999}
.brand{font-weight:700;font-size:12px;letter-spacing:2.5px;text-transform:uppercase;color:var(--amber);display:flex;align-items:center;gap:10px;margin-bottom:6px}
.brand svg{opacity:0.8}
p.sub{color:var(--text2);font-size:13px;margin-bottom:28px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px;max-width:600px;width:100%}
.card{display:block;text-decoration:none;color:var(--text0);background:var(--bg1);border:1px solid var(--border);border-radius:8px;padding:14px 16px;transition:border-color 0.2s,transform 0.15s}
.card:hover{border-color:var(--amber);transform:translateY(-2px)}
.card h2{font-size:14px;font-weight:600;margin-bottom:3px;color:var(--amber)}
.card p{font-size:11.5px;color:var(--text2);line-height:1.4}
@keyframes fi{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}
.card{animation:fi 0.4s cubic-bezier(0.22,1,0.36,1) both}
.card:nth-child(1){animation-delay:.04s}.card:nth-child(2){animation-delay:.08s}.card:nth-child(3){animation-delay:.12s}
.card:nth-child(4){animation-delay:.16s}.card:nth-child(5){animation-delay:.2s}.card:nth-child(6){animation-delay:.24s}
.card:nth-child(7){animation-delay:.28s}.card:nth-child(8){animation-delay:.32s}.card:nth-child(9){animation-delay:.36s}
</style>
</head>
<body>
  <div class="brand">
    <svg width="18" height="13" viewBox="0 0 18 13" fill="none"><rect width="18" height="2.4" rx="1.2" fill="currentColor"/><rect y="5" width="12" height="2.4" rx="1.2" fill="currentColor" opacity="0.55"/><rect y="10" width="7" height="2.4" rx="1.2" fill="currentColor" opacity="0.25"/></svg>
    Transit Tracker
  </div>
  <p class="sub">Available pages</p>
  <div class="grid">""" + cards + """</div>
</body>
</html>"""


def generate_monitor_html() -> str:
    """Generate the network monitor page."""
    return r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Transit Tracker — Network Monitor</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect width='10' height='3' rx='1.5' fill='%23e8a830'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-0: #06080e;
  --bg-1: #0c0f1a;
  --bg-2: #141828;
  --bg-3: #1c2038;
  --border: #1a1e35;
  --border-hover: #282e4a;
  --text-0: #eae8e4;
  --text-1: #9498b0;
  --text-2: #5c6080;
  --text-3: #353850;
  --amber: #e8a830;
  --amber-bg: rgba(232,168,48,0.08);
  --cyan: #3d80d0;
  --cyan-bg: rgba(61,128,208,0.08);
  --green: #40b868;
  --green-bg: rgba(64,184,104,0.08);
  --red: #d84050;
  --red-bg: rgba(216,64,80,0.08);
  --purple: #9060c0;
  --purple-bg: rgba(144,96,192,0.08);
}
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Outfit', system-ui, -apple-system, sans-serif;
  background: var(--bg-0);
  color: var(--text-0);
  line-height: 1.5;
  min-height: 100vh;
}
body::after {
  content: '';
  position: fixed;
  inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E");
  opacity: 0.018;
  pointer-events: none;
  z-index: 9999;
}
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* ── Nav ── */
.nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 28px; height: 50px;
  background: var(--bg-1); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 100;
}
.nav-left { display: flex; align-items: center; gap: 24px; }
.nav-brand {
  font-weight: 700; font-size: 12px; letter-spacing: 2.5px;
  text-transform: uppercase; color: var(--amber);
  display: flex; align-items: center; gap: 10px;
  white-space: nowrap;
}
.nav-brand svg { flex-shrink: 0; }
.nav-links { display: flex; gap: 2px; }
.nav-link {
  font-size: 12.5px; font-weight: 500; color: var(--text-2);
  text-decoration: none; padding: 5px 12px; border-radius: 5px;
  transition: all 0.2s;
}
.nav-link:hover { color: var(--text-0); background: var(--bg-2); }
.nav-link.active { color: var(--amber); background: var(--amber-bg); }
.nav-right { display: flex; align-items: center; gap: 14px; }
.live-badge {
  display: flex; align-items: center; gap: 6px;
  font-size: 11.5px; font-weight: 500; color: var(--green);
}
.live-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 8px rgba(64,184,104,0.5);
  animation: pulse 2.5s ease-in-out infinite;
}
.live-dot.off { background: var(--red); box-shadow: 0 0 8px rgba(216,64,80,0.4); animation: none; }
@keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.35; transform: scale(0.8); } }

/* ── Layout ── */
.main {
  display: grid; grid-template-columns: 1.15fr 1fr;
  gap: 0; height: calc(100vh - 50px);
}
.col-left { border-right: 1px solid var(--border); display: flex; flex-direction: column; }
.col-right { display: flex; flex-direction: column; overflow: hidden; }

.section-hdr {
  padding: 9px 18px; font-size: 10px; text-transform: uppercase;
  letter-spacing: 1px; color: var(--text-2); font-weight: 600;
  border-bottom: 1px solid var(--border); background: var(--bg-1);
  display: flex; align-items: center; gap: 8px;
}
.section-hdr .dot { width: 5px; height: 5px; border-radius: 50%; }
.section-hdr .meta {
  margin-left: auto; font-size: 10px; text-transform: none;
  letter-spacing: 0; color: var(--text-3);
  font-family: 'IBM Plex Mono', monospace;
}

/* ── Topology ── */
.topo-wrap { flex: 1; position: relative; overflow: hidden; }
svg.topo { width: 100%; height: 100%; }
svg.topo text { font-family: 'IBM Plex Mono', monospace; }

/* ── Trip table ── */
.trip-section { border-top: 1px solid var(--border); }
.trip-table {
  width: 100%; border-collapse: collapse;
  font-size: 11.5px; font-family: 'IBM Plex Mono', monospace;
}
.trip-table th {
  text-align: left; padding: 5px 14px; color: var(--text-3);
  font-weight: 500; font-size: 9px; text-transform: uppercase;
  letter-spacing: 0.8px; border-bottom: 1px solid var(--border);
  font-family: 'Outfit', sans-serif;
}
.trip-table td { padding: 4px 14px; border-bottom: 1px solid rgba(20,24,40,0.5); }
.trip-table tr:hover td { background: var(--bg-2); }
.rt-dot { font-size: 12px; }
.rt-dot.live { color: var(--green); }
.rt-dot.sched { color: var(--text-3); }
.route-badge {
  display: inline-block; padding: 1px 7px; border-radius: 3px;
  font-weight: 600; font-size: 10.5px;
}

/* ── Feed ── */
.feed-list {
  flex: 1; overflow-y: auto;
  font-size: 11.5px; font-family: 'IBM Plex Mono', monospace;
}
.feed-entry {
  display: grid; grid-template-columns: 66px 1fr; gap: 0;
  padding: 3px 16px; border-bottom: 1px solid rgba(20,24,40,0.5);
  transition: background 0.1s;
}
.feed-entry:hover { background: var(--bg-2); }
.feed-ts { color: var(--text-3); white-space: nowrap; font-size: 10.5px; }
.feed-body { display: flex; flex-direction: column; gap: 1px; }
.feed-dir { font-weight: 600; font-size: 11.5px; }
.feed-detail { color: var(--text-1); font-size: 11.5px; }
.feed-json {
  color: var(--text-3); font-size: 10px; max-height: 48px; overflow: hidden;
  text-overflow: ellipsis; white-space: pre-wrap; word-break: break-all;
  margin-top: 2px; padding: 3px 6px; background: var(--bg-0); border-radius: 3px;
}
.dir-send { color: var(--cyan); }
.dir-recv { color: var(--green); }
.dir-err { color: var(--red); }
.dir-throttle { color: var(--amber); }
.dir-connect { color: var(--cyan); }
.dir-heartbeat { color: var(--text-3); }
.toggle-json { font-size: 10px; cursor: pointer; color: var(--purple); padding: 0 4px; }
.toggle-json:hover { text-decoration: underline; }

/* ── Sim checkbox ── */
.sim-label {
  display: flex; align-items: center; gap: 5px;
  cursor: pointer; font-size: 11.5px; color: var(--text-2);
  font-weight: 500;
}
.sim-label input { accent-color: var(--amber); }

@media (max-width: 800px) {
  .main { grid-template-columns: 1fr; grid-template-rows: auto 1fr; }
  .col-left { border-right: none; border-bottom: 1px solid var(--border); max-height: 50vh; }
  .nav-links { display: none; }
}
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-left">
    <div class="nav-brand">
      <svg width="18" height="13" viewBox="0 0 18 13" fill="none">
        <rect width="18" height="2.4" rx="1.2" fill="currentColor"/>
        <rect y="5" width="12" height="2.4" rx="1.2" fill="currentColor" opacity="0.55"/>
        <rect y="10" width="7" height="2.4" rx="1.2" fill="currentColor" opacity="0.25"/>
      </svg>
      Transit Tracker
    </div>
    <div class="nav-links">
      <a href="/transit-tracker/dashboard" class="nav-link">Dashboard</a>
      <a href="/transit-tracker/monitor" class="nav-link active">Monitor</a>
      <a href="/transit-tracker/simulator" class="nav-link">Simulator</a>
      <a href="/transit-tracker/spec" class="nav-link">API</a>
    </div>
  </div>
  <div class="nav-right">
    <label class="sim-label">
      <input type="checkbox" id="sim-toggle"> LED Simulator
    </label>
    <div class="live-badge">
      <span class="live-dot" id="live-dot"></span>
      <span id="conn-label">Connecting</span>
    </div>
  </div>
</nav>

<div class="main">
  <div class="col-left">
    <div class="section-hdr">
      <span class="dot" style="background:var(--cyan)"></span> Network Topology
    </div>
    <div class="topo-wrap">
      <svg class="topo" id="topo-svg" viewBox="0 0 600 500" preserveAspectRatio="xMidYMid meet"></svg>
      <iframe id="sim-iframe" src="about:blank" style="display:none;position:absolute;border:none;border-radius:4px;background:#000;z-index:10"></iframe>
    </div>
    <div class="trip-section">
      <div class="section-hdr">
        <span class="dot" style="background:var(--green)"></span> Last Schedule Push
        <span class="meta" id="trip-age"></span>
      </div>
      <div style="max-height:200px;overflow-y:auto">
        <table class="trip-table">
          <thead><tr><th>Route</th><th>Headsign</th><th style="text-align:right">ETA</th><th>RT</th><th>Stop</th></tr></thead>
          <tbody id="trip-body"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div class="col-right">
    <div class="section-hdr">
      <span class="dot" style="background:var(--amber)"></span> Message Flow
      <span class="meta" id="msg-count">0 events</span>
    </div>
    <div class="feed-list" id="feed-list"></div>
  </div>
</div>

<script>
(function() {
'use strict';

/* ── Simulator toggle ── */
var simToggle = document.getElementById('sim-toggle');
var simActive = false;
var simLoaded = false;
simToggle.addEventListener('change', function() {
  simActive = this.checked;
  if (!simActive) {
    /* Destroy iframe to close WebSocket connection */
    var iframe = document.getElementById('sim-iframe');
    iframe.src = 'about:blank';
    simLoaded = false;
  }
  renderTopo();
});

/* ── State ── */
var state = {}, prevState = {}, events = [];
var showJson = {};
var MAX_EVENTS = 200;

/* ── Helpers ── */
function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
function ago(ts) {
  if (!ts) return 'never';
  var d = (Date.now() / 1000) - ts;
  if (d < 2) return 'just now';
  if (d < 60) return Math.floor(d) + 's ago';
  if (d < 3600) return Math.floor(d / 60) + 'm ago';
  return (d / 3600).toFixed(1) + 'h ago';
}
function fmtTime(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function mins(at) {
  var d = (at - Date.now() / 1000) / 60;
  if (d <= 0) return 'Now';
  return Math.ceil(d) + 'm';
}

/* ── Events ── */
function addEvent(kind, dir, detail, jsonPayload) {
  events.push({ ts: Date.now() / 1000, kind: kind, dir: dir, detail: detail, json: jsonPayload || null });
  if (events.length > MAX_EVENTS) events = events.slice(-MAX_EVENTS);
}

function detectChanges(cur, prev) {
  var lu = cur.last_update || 0, plu = prev.last_update || 0;
  if (lu && lu !== plu) {
    var lm = cur.last_message || {};
    var trips = ((lm.data || {}).trips) || [];
    addEvent('send', 'server \u2192 clients', trips.length + ' trips pushed',
      JSON.stringify(lm, null, 2));
  }
  var cc = cur.client_count || 0, pc = prev.client_count || 0;
  if (cc > pc) addEvent('connect', '+' + (cc - pc) + ' client(s)', 'connected (total ' + cc + ')',
    JSON.stringify(cur.clients || [], null, 2));
  if (cc < pc) addEvent('err', '-' + (pc - cc) + ' client(s)', 'disconnected (total ' + cc + ')');
  var rl = cur.is_rate_limited, prl = prev.is_rate_limited;
  if (rl && !prl) addEvent('throttle', 'OBA \u2192 server', '429 rate limited');
  if (prl && !rl) addEvent('recv', 'OBA \u2192 server', 'Rate limit cleared');
  var ac = cur.api_calls_total || 0, pac = prev.api_calls_total || 0;
  if (ac > pac) addEvent('recv', 'server \u2192 OBA', (ac - pac) + ' API call(s) (total ' + ac + ')');
  var hb = cur.heartbeat || 0, phb = prev.heartbeat || 0;
  if (hb && hb !== phb) addEvent('heartbeat', 'server \u2192 clients', 'heartbeat');
}

/* ── SVG Topology ── */
function renderTopo() {
  var svg = document.getElementById('topo-svg');
  var clients = state.clients || [];
  var cc = state.client_count || 0;
  var running = state.status === 'active';
  var rl = state.is_rate_limited;
  var apiCalls = state.api_calls_total || 0;
  var throttle = state.throttle_total || 0;
  var refresh = state.refresh_interval || 30;
  var msgs = state.messages_processed || 0;
  var upH = state.uptime_hours || 0;
  var upStr = upH >= 1 ? upH.toFixed(1) + 'h' : Math.round(upH * 60) + 'm';
  var rows = Math.max(cc, 1);
  var simH = simActive ? 90 : 0;
  var svgH = 290 + rows * 56 + simH;
  svg.setAttribute('viewBox', '0 0 600 ' + svgH);

  var h = '';

  /* Defs */
  h += '<defs>';
  h += '<marker id="a" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#3d80d0"/></marker>';
  h += '<marker id="ag" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#40b868"/></marker>';
  h += '<filter id="glow"><feGaussianBlur stdDeviation="4" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>';
  h += '</defs>';

  /* OBA API node */
  var oy = 18;
  h += '<rect x="185" y="' + oy + '" width="230" height="72" rx="8" fill="#0c0f1a" stroke="' + (rl ? '#e8a830' : '#1a1e35') + '" stroke-width="' + (rl ? 2 : 1) + '"/>';
  h += '<text x="300" y="' + (oy + 21) + '" text-anchor="middle" fill="#e8a830" font-size="11.5" font-weight="700">OneBusAway API</text>';
  h += '<text x="300" y="' + (oy + 40) + '" text-anchor="middle" fill="' + (rl ? '#d84050' : '#40b868') + '" font-size="10.5" font-weight="600">' + (rl ? 'THROTTLED' : 'HEALTHY') + '</text>';
  h += '<text x="300" y="' + (oy + 57) + '" text-anchor="middle" fill="#353850" font-size="9.5">Calls: ' + apiCalls + '  \u00b7  429s: ' + throttle + '</text>';

  /* Wire: OBA -> Server */
  var wy1 = oy + 72, wy2 = oy + 128;
  if (rl) {
    h += '<line x1="300" y1="' + wy1 + '" x2="300" y2="' + wy2 + '" stroke="#d84050" stroke-width="1.5" stroke-dasharray="5,4"/>';
    h += '<text x="316" y="' + (wy1 + 26) + '" fill="#d84050" font-size="9.5" font-weight="600">429 BLOCKED</text>';
  } else {
    h += '<line x1="300" y1="' + wy1 + '" x2="300" y2="' + wy2 + '" stroke="#3d80d0" stroke-width="1.5" stroke-dasharray="5,4" marker-end="url(#a)">';
    h += '<animate attributeName="stroke-dashoffset" from="18" to="0" dur="0.8s" repeatCount="indefinite"/>';
    h += '</line>';
    h += '<text x="316" y="' + (wy1 + 26) + '" fill="#353850" font-size="9.5">arrivals / ' + refresh + 's</text>';
  }

  /* Server node */
  var sy = wy2;
  h += '<rect x="165" y="' + sy + '" width="270" height="90" rx="8" fill="#0c0f1a" stroke="' + (running ? '#3d80d0' : '#d84050') + '" stroke-width="' + (running ? 1.5 : 2) + '"/>';
  if (running) {
    h += '<rect x="165" y="' + sy + '" width="270" height="90" rx="8" fill="none" stroke="#3d80d0" stroke-width="1" opacity="0.12" filter="url(#glow)"/>';
  }
  h += '<circle cx="184" cy="' + (sy + 18) + '" r="3.5" fill="' + (running ? '#40b868' : '#d84050') + '"/>';
  h += '<text x="193" y="' + (sy + 22) + '" fill="' + (running ? '#40b868' : '#d84050') + '" font-size="11" font-weight="700">' + (running ? 'RUNNING' : 'STOPPED') + '</text>';
  h += '<text x="182" y="' + (sy + 40) + '" fill="#3d80d0" font-size="10.5">Transit Proxy :8000</text>';
  h += '<text x="182" y="' + (sy + 57) + '" fill="#353850" font-size="9.5">Up: ' + esc(upStr) + '  \u00b7  Msgs: ' + msgs + '</text>';
  h += '<text x="182" y="' + (sy + 72) + '" fill="#353850" font-size="9.5">Refresh: ' + refresh + 's  \u00b7  Clients: ' + cc + '</text>';

  /* Clients — bus topology: vertical trunk + horizontal branches */
  var cStartY = sy + 90 + 26;
  var trunkX = 155;
  var clientBoxX = 185;
  var clientBoxW = 240;
  var clientBoxH = 40;
  var clientSpacing = 52;
  var simEmbedH = 75;
  var simEmbedW = 300;

  /* When sim is active, filter WebSimulator clients out of the normal list
     and render them as part of the compound simulator node instead */
  var displayClients = [];
  var simClientInfo = null;
  for (var i = 0; i < clients.length; i++) {
    if (simActive && (clients[i].name || '') === 'WebSimulator') {
      if (!simClientInfo) simClientInfo = clients[i]; /* take first match */
    } else {
      displayClients.push(clients[i]);
    }
  }
  var simConnected = !!simClientInfo;

  /* Total visual nodes */
  var totalNodes = displayClients.length + (simActive ? 1 : 0);

  if (totalNodes === 0) {
    h += '<line x1="300" y1="' + (sy + 90) + '" x2="300" y2="' + cStartY + '" stroke="#1a1e35" stroke-width="1" stroke-dasharray="3,4"/>';
    h += '<text x="300" y="' + (cStartY + 16) + '" text-anchor="middle" fill="#353850" font-size="10.5" font-style="italic">No clients connected</text>';
  } else {
    /* Calculate Y positions — sim node gets extra height for canvas */
    var simNodeH = clientBoxH + 3 + simEmbedH;
    var nodeYs = [];
    var nodeTypes = []; /* 'client' or 'sim' */
    var curY = cStartY;

    /* Sim node goes first when active */
    if (simActive) {
      nodeYs.push(curY);
      nodeTypes.push('sim');
      curY += simNodeH + (clientSpacing - clientBoxH);
    }
    for (var i = 0; i < displayClients.length; i++) {
      nodeYs.push(curY);
      nodeTypes.push('client');
      curY += clientSpacing;
    }

    /* Trunk endpoint = midpoint of last node */
    var lastIdx = nodeYs.length - 1;
    var lastNodeMidY;
    if (nodeTypes[lastIdx] === 'sim') {
      lastNodeMidY = nodeYs[lastIdx] + clientBoxH / 2;
    } else {
      lastNodeMidY = nodeYs[lastIdx] + clientBoxH / 2;
    }

    /* Connector: server bottom center to trunk top */
    h += '<line x1="300" y1="' + (sy + 90) + '" x2="' + trunkX + '" y2="' + (sy + 90) + '" stroke="#40b868" stroke-width="1.5" stroke-dasharray="5,4"/>';

    /* Vertical trunk */
    h += '<line x1="' + trunkX + '" y1="' + (sy + 90) + '" x2="' + trunkX + '" y2="' + lastNodeMidY + '" stroke="#40b868" stroke-width="1.5" stroke-dasharray="5,4">';
    h += '<animate attributeName="stroke-dashoffset" from="0" to="-18" dur="1.2s" repeatCount="indefinite"/>';
    h += '</line>';

    /* Render each node */
    var clientIdx = 0;
    for (var ni = 0; ni < nodeYs.length; ni++) {
      var ny = nodeYs[ni];
      var branchY = ny + clientBoxH / 2;

      if (nodeTypes[ni] === 'sim') {
        /* ── Compound simulator node ── */
        /* Green branch to info box (connection line ends at client box, not LED panel) */
        var branchColor = simConnected ? '#40b868' : '#e8a830';
        h += '<line x1="' + trunkX + '" y1="' + branchY + '" x2="' + clientBoxX + '" y2="' + branchY + '" stroke="' + branchColor + '" stroke-width="1.5" stroke-dasharray="5,4" marker-end="url(#ag)">';
        h += '<animate attributeName="stroke-dashoffset" from="0" to="-18" dur="1.2s" repeatCount="indefinite"/>';
        h += '</line>';

        /* Info header box */
        var infoStroke = simConnected ? '#40b868' : '#e8a830';
        var statusText = simConnected ? 'Connected' : 'Connecting\u2026';
        var statusColor = simConnected ? '#40b868' : '#e8a830';
        var simAddr = simClientInfo ? (simClientInfo.address || '?').split(':')[0] : '\u2014';
        var simSubs = simClientInfo ? (simClientInfo.subscriptions || 0) : 0;

        h += '<rect x="' + clientBoxX + '" y="' + ny + '" width="' + simEmbedW + '" height="' + clientBoxH + '" rx="6" fill="#0c0f1a" stroke="' + infoStroke + '" stroke-width="1.5"/>';
        h += '<text x="' + (clientBoxX + 12) + '" y="' + (ny + 16) + '" fill="#eae8e4" font-size="10.5" font-weight="600">WebSimulator</text>';
        h += '<text x="' + (clientBoxX + simEmbedW - 8) + '" y="' + (ny + 16) + '" text-anchor="end" fill="' + statusColor + '" font-size="9.5" font-weight="600">\u25CF ' + statusText + '</text>';
        h += '<text x="' + (clientBoxX + 12) + '" y="' + (ny + 30) + '" fill="#353850" font-size="9.5">' + esc(simAddr) + ' \u00b7 ' + simSubs + ' subs</text>';

        /* LED canvas below, flush against info box */
        var simY = ny + clientBoxH + 3;
        h += '<rect x="' + clientBoxX + '" y="' + simY + '" width="' + simEmbedW + '" height="' + simEmbedH + '" rx="5" fill="#000" stroke="#e8a830" stroke-width="3" id="sim-placeholder"/>';
        h += '<rect x="' + clientBoxX + '" y="' + simY + '" width="' + simEmbedW + '" height="' + simEmbedH + '" rx="5" fill="none" stroke="#e8a830" stroke-width="2" opacity="0.15" filter="url(#glow)"/>';

      } else {
        /* ── Normal client node ── */
        var c = displayClients[clientIdx++];
        var name = c.name || 'Unknown';
        var addr = (c.address || '?').split(':')[0];
        var subs = c.subscriptions || 0;
        var isLocal = addr === '127.0.0.1' || addr === 'localhost';
        var icon = isLocal ? '\uD83D\uDDA5\uFE0F' : '\uD83D\uDCFA';

        h += '<line x1="' + trunkX + '" y1="' + branchY + '" x2="' + clientBoxX + '" y2="' + branchY + '" stroke="#40b868" stroke-width="1.5" stroke-dasharray="5,4" marker-end="url(#ag)">';
        h += '<animate attributeName="stroke-dashoffset" from="0" to="-18" dur="1.2s" repeatCount="indefinite"/>';
        h += '</line>';

        h += '<rect x="' + clientBoxX + '" y="' + ny + '" width="' + clientBoxW + '" height="' + clientBoxH + '" rx="6" fill="#0c0f1a" stroke="#1a1e35" stroke-width="1"/>';
        h += '<text x="' + (clientBoxX + 17) + '" y="' + (ny + 16) + '" fill="#eae8e4" font-size="13">' + icon + '</text>';
        h += '<text x="' + (clientBoxX + 39) + '" y="' + (ny + 16) + '" fill="#eae8e4" font-size="10.5" font-weight="600">' + esc(name) + '</text>';
        h += '<text x="' + (clientBoxX + 39) + '" y="' + (ny + 30) + '" fill="#353850" font-size="9.5">' + esc(addr) + ' \u00b7 ' + subs + ' subs</text>';
      }
    }
  }

  svg.innerHTML = h;

  /* Position the external iframe over the SVG placeholder */
  var simIframe = document.getElementById('sim-iframe');
  var placeholder = document.getElementById('sim-placeholder');
  if (simActive && placeholder) {
    /* Load iframe once */
    if (!simLoaded) {
      simLoaded = true;
      simIframe.src = '/transit-tracker/simulator?embed=1';
    }
    /* Map SVG coords to screen coords */
    var svgEl = document.getElementById('topo-svg');
    var svgRect = svgEl.getBoundingClientRect();
    var viewBox = svgEl.viewBox.baseVal;
    var scaleX = svgRect.width / viewBox.width;
    var scaleY = svgRect.height / viewBox.height;
    var pBox = placeholder.getBBox();
    simIframe.style.display = 'block';
    simIframe.style.left = (pBox.x * scaleX) + 'px';
    simIframe.style.top = (pBox.y * scaleY) + 'px';
    simIframe.style.width = (pBox.width * scaleX) + 'px';
    simIframe.style.height = (pBox.height * scaleY) + 'px';
  } else {
    simIframe.style.display = 'none';
  }
}

/* ── Trip table ── */
function renderTrips() {
  var lm = state.last_message || {};
  var trips = ((lm.data || {}).trips) || [];
  var body = document.getElementById('trip-body');
  var ageEl = document.getElementById('trip-age');

  if (!trips.length) {
    body.innerHTML = '<tr><td colspan="5" style="color:var(--text-3);font-style:italic;padding:12px">Waiting for data\u2026</td></tr>';
    ageEl.textContent = '';
    return;
  }
  ageEl.textContent = ago(state.last_update);

  body.innerHTML = trips.slice(0, 10).map(function(t) {
    var rt = t.isRealtime;
    var at = t.arrivalTime > 1e12 ? t.arrivalTime / 1000 : t.arrivalTime;
    var color = t.routeColor ? '#' + t.routeColor : 'var(--cyan)';
    return '<tr>' +
      '<td><span class="route-badge" style="background:' + color + '15;color:' + color + '">' + esc(t.routeName || '?') + '</span></td>' +
      '<td>' + esc(t.headsign || '') + '</td>' +
      '<td style="text-align:right;font-weight:600">' + mins(at) + '</td>' +
      '<td><span class="rt-dot ' + (rt ? 'live' : 'sched') + '">' + (rt ? '\u25C9' : '\u25CB') + '</span></td>' +
      '<td style="color:var(--text-3)">' + esc(t.stopId || '') + '</td></tr>';
  }).join('');
}

/* ── Message feed ── */
function renderFeed() {
  var list = document.getElementById('feed-list');
  document.getElementById('msg-count').textContent = events.length + ' events';

  var atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  var vis = events.slice(-100);

  list.innerHTML = vis.map(function(ev, i) {
    var idx = events.length - vis.length + i;
    var ts = fmtTime(ev.ts);
    var dc = 'dir-send';
    if (ev.kind === 'recv') dc = 'dir-recv';
    else if (ev.kind === 'err') dc = 'dir-err';
    else if (ev.kind === 'throttle') dc = 'dir-throttle';
    else if (ev.kind === 'connect') dc = 'dir-connect';
    else if (ev.kind === 'heartbeat') dc = 'dir-heartbeat';

    var jh = '';
    if (ev.json) {
      var isVisible = showJson[idx];
      jh = '<span class="toggle-json" onclick="window._toggleJson(' + idx + ')">' + (isVisible ? '[\u2212]' : '[json]') + '</span>';
      if (isVisible) {
        jh += '<div class="feed-json">' + esc(ev.json.substring(0, 800)) + '</div>';
      }
    }

    return '<div class="feed-entry">' +
      '<span class="feed-ts">' + ts + '</span>' +
      '<div class="feed-body">' +
        '<div><span class="feed-dir ' + dc + '">' + esc(ev.dir) + '</span> ' +
        '<span class="feed-detail">' + esc(ev.detail) + '</span>' + jh + '</div>' +
      '</div></div>';
  }).join('');

  if (atBottom) list.scrollTop = list.scrollHeight;
}

window._toggleJson = function(idx) { showJson[idx] = !showJson[idx]; renderFeed(); };

/* ── Polling ── */
var lastLogTs = 0;

function poll() {
  fetch('/transit-tracker/api/status?full=1').then(function(r) { return r.json(); }).then(function(cur) {
    if (cur.status === 'unavailable' || cur.status === 'error') {
      document.getElementById('live-dot').className = 'live-dot off';
      document.getElementById('conn-label').textContent = 'Unavailable';
      return;
    }
    document.getElementById('live-dot').className = 'live-dot';
    document.getElementById('conn-label').textContent = 'Live';

    detectChanges(cur, prevState);
    prevState = Object.assign({}, state);
    state = cur;

    renderTopo();
    renderTrips();
    renderFeed();
  }).catch(function() {
    document.getElementById('live-dot').className = 'live-dot off';
    document.getElementById('conn-label').textContent = 'Error';
  });
}

function pollLogs() {
  fetch('/transit-tracker/api/logs?since=' + lastLogTs + '&limit=50').then(function(r) { return r.json(); }).then(function(data) {
    if (data.logs && data.logs.length) {
      for (var j = 0; j < data.logs.length; j++) {
        var entry = data.logs[j];
        var comp = entry.component || entry.logger || '';
        var msg = entry.msg || '';
        var level = entry.level || 'INFO';

        if (msg.indexOf('429') >= 0 || msg.indexOf('rate limit') >= 0) {
          addEvent('throttle', 'OBA \u2192 server', msg);
        } else if (msg.indexOf('connected') >= 0 && comp.indexOf('server') >= 0) {
          addEvent('connect', 'client \u2192 server', msg);
        } else if (msg.indexOf('disconnect') >= 0 && comp.indexOf('server') >= 0) {
          addEvent('err', 'server \u2715 client', msg);
        } else if (msg.indexOf('subscribe') >= 0) {
          addEvent('recv', 'client \u2192 server', msg,
            entry.pairs ? '{"pairs":' + entry.pairs + '}' : null);
        } else if (level === 'ERROR') {
          addEvent('err', comp, msg);
        }
      }
      lastLogTs = data.logs[data.logs.length - 1].ts + 0.001;
      renderFeed();
    }
  }).catch(function() {});
}

/* ── Boot ── */
poll();
pollLogs();
setInterval(poll, 2000);
setInterval(pollLogs, 3000);

})();
</script>
</body>
</html>
"""


def generate_dashboard_html() -> str:
    """Generate the observability dashboard."""
    return r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>Transit Tracker — Dashboard</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect width='10' height='3' rx='1.5' fill='%23e8a830'/></svg>">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-0: #06080e;
  --bg-1: #0c0f1a;
  --bg-2: #141828;
  --bg-3: #1c2038;
  --border: #1a1e35;
  --border-hover: #282e4a;
  --text-0: #eae8e4;
  --text-1: #9498b0;
  --text-2: #5c6080;
  --text-3: #353850;
  --amber: #e8a830;
  --amber-bg: rgba(232,168,48,0.08);
  --cyan: #3d80d0;
  --cyan-bg: rgba(61,128,208,0.08);
  --green: #40b868;
  --green-bg: rgba(64,184,104,0.08);
  --red: #d84050;
  --red-bg: rgba(216,64,80,0.08);
  --purple: #9060c0;
  --purple-bg: rgba(144,96,192,0.08);
}
*, *::before, *::after { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: 'Outfit', system-ui, -apple-system, sans-serif;
  background: var(--bg-0);
  color: var(--text-0);
  line-height: 1.5;
  min-height: 100vh;
}
body::after {
  content: '';
  position: fixed;
  inset: 0;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 200 200' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.5'/%3E%3C/svg%3E");
  opacity: 0.018;
  pointer-events: none;
  z-index: 9999;
}
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }

/* ── Nav ── */
.nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 28px; height: 50px;
  background: var(--bg-1); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 100;
}
.nav-left { display: flex; align-items: center; gap: 24px; }
.nav-brand {
  font-weight: 700; font-size: 12px; letter-spacing: 2.5px;
  text-transform: uppercase; color: var(--amber);
  display: flex; align-items: center; gap: 10px;
  white-space: nowrap;
}
.nav-brand svg { flex-shrink: 0; }
.nav-links { display: flex; gap: 2px; }
.nav-link {
  font-size: 12.5px; font-weight: 500; color: var(--text-2);
  text-decoration: none; padding: 5px 12px; border-radius: 5px;
  transition: all 0.2s;
}
.nav-link:hover { color: var(--text-0); background: var(--bg-2); }
.nav-link.active { color: var(--amber); background: var(--amber-bg); }
.nav-right { display: flex; align-items: center; gap: 16px; }
.live-badge {
  display: flex; align-items: center; gap: 6px;
  font-size: 11.5px; font-weight: 500; color: var(--green);
}
.live-dot {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green);
  box-shadow: 0 0 8px rgba(64,184,104,0.5);
  animation: pulse 2.5s ease-in-out infinite;
}
.live-dot.off { background: var(--red); box-shadow: 0 0 8px rgba(216,64,80,0.4); animation: none; }
@keyframes pulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: 0.35; transform: scale(0.8); } }
.time-range { display: flex; border: 1px solid var(--border); border-radius: 5px; overflow: hidden; }
.time-range button {
  background: none; border: none; color: var(--text-2);
  padding: 3px 10px; font-size: 11px; font-weight: 600;
  cursor: pointer; transition: all 0.15s;
  font-family: 'Outfit', sans-serif; letter-spacing: 0.3px;
}
.time-range button:hover:not(.active) { color: var(--text-0); }
.time-range button.active { background: var(--amber); color: #080a10; }
.time-range button + button { border-left: 1px solid var(--border); }

/* ── Info strip ── */
.info-strip {
  display: flex; align-items: center; gap: 16px;
  padding: 8px 28px;
  border-bottom: 1px solid var(--border);
  font-size: 11.5px; color: var(--text-2);
  background: var(--bg-1);
}
.info-chip {
  display: flex; align-items: center; gap: 6px;
  padding: 2px 10px; border-radius: 4px;
  background: var(--bg-2); border: 1px solid var(--border);
}
.info-chip .label { color: var(--text-2); font-weight: 400; }
.info-chip .val {
  color: var(--text-0); font-weight: 600;
  font-family: 'IBM Plex Mono', monospace; font-size: 11px;
}
.info-chip .val.amber { color: var(--amber); }
.info-chip .val.green { color: var(--green); }

/* ── Container ── */
.container { padding: 18px 28px; max-width: 1440px; margin: 0 auto; }

/* ── Stat grid ── */
.stat-grid {
  display: grid; gap: 10px; margin-bottom: 14px;
  grid-template-columns: repeat(6, 1fr);
}
.stat-card {
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 16px;
  position: relative; overflow: hidden;
  transition: border-color 0.2s, transform 0.15s;
}
.stat-card:hover { border-color: var(--border-hover); transform: translateY(-1px); }
.stat-card::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0;
  width: 3px; border-radius: 3px 0 0 3px;
}
.stat-card[data-accent="amber"]::before { background: var(--amber); }
.stat-card[data-accent="cyan"]::before { background: var(--cyan); }
.stat-card[data-accent="green"]::before { background: var(--green); }
.stat-card[data-accent="red"]::before { background: var(--red); }
.stat-card[data-accent="purple"]::before { background: var(--purple); }
.stat-label {
  font-size: 9.5px; text-transform: uppercase; letter-spacing: 1px;
  color: var(--text-2); font-weight: 600; margin-bottom: 6px;
}
.stat-value {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 24px; font-weight: 700;
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}
.stat-sub { font-size: 10.5px; color: var(--text-2); margin-top: 4px; }
.stat-value.amber { color: var(--amber); }
.stat-value.cyan { color: var(--cyan); }
.stat-value.green { color: var(--green); }
.stat-value.red { color: var(--red); }
.stat-value.purple { color: var(--purple); }

/* ── Charts ── */
.chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
.chart-card {
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px 16px; overflow: hidden;
}
.chart-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 10px;
}
.chart-title {
  font-size: 11.5px; font-weight: 600; color: var(--text-1);
  display: flex; align-items: center; gap: 7px;
}
.chart-title .dot { width: 5px; height: 5px; border-radius: 50%; }
.chart-latest {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px; font-weight: 600;
}
canvas { width: 100% !important; height: 130px !important; display: block; }

/* ── Log panel ── */
.log-panel {
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: 8px; overflow: hidden;
}
.log-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 16px; border-bottom: 1px solid var(--border);
}
.log-title { font-size: 12px; font-weight: 600; color: var(--text-1); }
.log-count {
  font-family: 'IBM Plex Mono', monospace; font-size: 10px;
  color: var(--text-3); margin-left: 8px; font-weight: 400;
}
.log-filters { display: flex; gap: 3px; }
.log-filters button {
  background: var(--bg-2); border: 1px solid var(--border);
  color: var(--text-2); padding: 2px 9px; border-radius: 4px;
  font-size: 10.5px; font-weight: 500; cursor: pointer;
  font-family: 'Outfit', sans-serif; transition: all 0.15s;
}
.log-filters button.active { background: var(--amber); border-color: var(--amber); color: #080a10; }
.log-filters button:hover:not(.active) { border-color: var(--text-2); }
.log-list {
  max-height: 320px; overflow-y: auto;
  font-family: 'IBM Plex Mono', monospace; font-size: 11.5px; line-height: 1.7;
}
.log-entry {
  padding: 1px 16px; border-bottom: 1px solid rgba(20,24,40,0.6);
  display: flex; gap: 10px; transition: background 0.1s;
}
.log-entry:hover { background: var(--bg-2); }
.log-ts { color: var(--text-3); white-space: nowrap; min-width: 70px; }
.log-level { font-weight: 600; min-width: 40px; text-align: center; }
.log-level.DEBUG { color: var(--text-3); }
.log-level.INFO { color: var(--cyan); }
.log-level.WARNING { color: var(--amber); }
.log-level.ERROR { color: var(--red); }
.log-comp { color: var(--purple); min-width: 48px; opacity: 0.6; }
.log-msg { color: var(--text-1); flex: 1; word-break: break-all; }

/* ── Responsive ── */
@media (max-width: 1100px) {
  .stat-grid { grid-template-columns: repeat(3, 1fr); }
}
@media (max-width: 900px) {
  .chart-grid { grid-template-columns: 1fr; }
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .info-strip { flex-wrap: wrap; gap: 8px; }
}
@media (max-width: 500px) {
  .container { padding: 12px; }
  .nav { padding: 0 14px; }
  .stat-grid { grid-template-columns: 1fr; }
  .stat-value { font-size: 20px; }
  .nav-links { display: none; }
}

/* ── Animations ── */
@keyframes fadeSlideIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: none; }
}
.stat-card, .chart-card, .log-panel { animation: fadeSlideIn 0.5s cubic-bezier(0.22,1,0.36,1) both; }
.stat-card:nth-child(1) { animation-delay: 0.04s; }
.stat-card:nth-child(2) { animation-delay: 0.08s; }
.stat-card:nth-child(3) { animation-delay: 0.12s; }
.stat-card:nth-child(4) { animation-delay: 0.16s; }
.stat-card:nth-child(5) { animation-delay: 0.20s; }
.stat-card:nth-child(6) { animation-delay: 0.24s; }
.chart-card:nth-child(1) { animation-delay: 0.30s; }
.chart-card:nth-child(2) { animation-delay: 0.34s; }
.chart-card:nth-child(3) { animation-delay: 0.38s; }
.chart-card:nth-child(4) { animation-delay: 0.42s; }
.log-panel { animation-delay: 0.48s; }
</style>
</head>
<body>

<nav class="nav">
  <div class="nav-left">
    <div class="nav-brand">
      <svg width="18" height="13" viewBox="0 0 18 13" fill="none">
        <rect width="18" height="2.4" rx="1.2" fill="currentColor"/>
        <rect y="5" width="12" height="2.4" rx="1.2" fill="currentColor" opacity="0.55"/>
        <rect y="10" width="7" height="2.4" rx="1.2" fill="currentColor" opacity="0.25"/>
      </svg>
      Transit Tracker
    </div>
    <div class="nav-links">
      <a href="/transit-tracker/dashboard" class="nav-link active">Dashboard</a>
      <a href="/transit-tracker/monitor" class="nav-link">Monitor</a>
      <a href="/transit-tracker/simulator" class="nav-link">Simulator</a>
      <a href="/transit-tracker/spec" class="nav-link">API</a>
    </div>
  </div>
  <div class="nav-right">
    <div class="time-range">
      <button class="active" data-range="300">5m</button>
      <button data-range="900">15m</button>
      <button data-range="1800">30m</button>
      <button data-range="0">All</button>
    </div>
    <div class="live-badge">
      <span class="live-dot" id="live-dot"></span>
      <span id="live-label">Live</span>
    </div>
  </div>
</nav>

<div class="info-strip">
  <div class="info-chip">
    <span class="label">Profile</span>
    <span class="val amber" id="i-profile">&mdash;</span>
  </div>
  <div class="info-chip">
    <span class="label">API Key</span>
    <span class="val" id="i-apikey">&mdash;</span>
  </div>
  <div class="info-chip">
    <span class="label">Refresh</span>
    <span class="val" id="i-refresh">&mdash;</span>
  </div>
</div>

<div class="container">
  <div class="stat-grid">
    <div class="stat-card" data-accent="green">
      <div class="stat-label">Uptime</div>
      <div class="stat-value green" id="s-uptime">&mdash;</div>
      <div class="stat-sub" id="s-uptime-sub"></div>
    </div>
    <div class="stat-card" data-accent="cyan">
      <div class="stat-label">Active Clients</div>
      <div class="stat-value cyan" id="s-clients">0</div>
      <div class="stat-sub" id="s-clients-sub"></div>
    </div>
    <div class="stat-card" data-accent="purple">
      <div class="stat-label">Messages Sent</div>
      <div class="stat-value purple" id="s-msgs">0</div>
      <div class="stat-sub" id="s-msgs-sub"></div>
    </div>
    <div class="stat-card" data-accent="amber">
      <div class="stat-label">API Calls</div>
      <div class="stat-value amber" id="s-api">0</div>
      <div class="stat-sub" id="s-api-sub"></div>
    </div>
    <div class="stat-card" data-accent="amber">
      <div class="stat-label">429 Throttles</div>
      <div class="stat-value" id="s-throttle">0</div>
      <div class="stat-sub" id="s-throttle-sub"></div>
    </div>
    <div class="stat-card" data-accent="red">
      <div class="stat-label">API Errors</div>
      <div class="stat-value" id="s-errors">0</div>
      <div class="stat-sub" id="s-errors-sub"></div>
    </div>
  </div>

  <div class="chart-grid">
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title"><span class="dot" style="background:var(--cyan)"></span>API Latency</div>
        <span class="chart-latest" id="cl-latency" style="color:var(--cyan)">&mdash;</span>
      </div>
      <canvas id="chart-latency"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title"><span class="dot" style="background:var(--green)"></span>Active Clients</div>
        <span class="chart-latest" id="cl-clients" style="color:var(--green)">&mdash;</span>
      </div>
      <canvas id="chart-clients"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title"><span class="dot" style="background:var(--purple)"></span>Refresh Interval</div>
        <span class="chart-latest" id="cl-interval" style="color:var(--purple)">&mdash;</span>
      </div>
      <canvas id="chart-interval"></canvas>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <div class="chart-title"><span class="dot" style="background:var(--amber)"></span>Throttle Rate</div>
        <span class="chart-latest" id="cl-throttle" style="color:var(--amber)">&mdash;</span>
      </div>
      <canvas id="chart-throttle"></canvas>
    </div>
  </div>

  <div class="log-panel">
    <div class="log-header">
      <div class="log-title">Event Log <span class="log-count" id="log-count"></span></div>
      <div class="log-filters">
        <button class="active" data-level="all">All</button>
        <button data-level="ERROR">Error</button>
        <button data-level="WARNING">Warn</button>
        <button data-level="INFO">Info</button>
        <button data-level="DEBUG">Debug</button>
      </div>
    </div>
    <div class="log-list" id="log-list"></div>
  </div>
</div>

<script>
(function() {
'use strict';

/* ── Smooth Chart Renderer ── */
class Chart {
  constructor(canvas, color) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.color = color;
    this.data = [];
    this._resize();
    window.addEventListener('resize', () => this._resize());
  }
  _resize() {
    var r = this.canvas.parentElement.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    this.canvas.width = r.width * dpr;
    this.canvas.height = 130 * dpr;
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.w = r.width;
    this.h = 130;
    this.draw();
  }
  setData(pts) { this.data = pts; this.draw(); }
  draw() {
    var ctx = this.ctx, w = this.w, h = this.h, d = this.data;
    ctx.clearRect(0, 0, w, h);
    if (d.length < 2) {
      ctx.fillStyle = '#353850';
      ctx.font = '500 11px Outfit, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText('Awaiting data\u2026', w / 2, h / 2);
      return;
    }
    /* Grid */
    ctx.strokeStyle = '#111428';
    ctx.lineWidth = 1;
    for (var g = 1; g < 4; g++) {
      var gy = (h / 4) * g;
      ctx.beginPath(); ctx.moveTo(0, gy); ctx.lineTo(w, gy); ctx.stroke();
    }
    var vals = d.map(function(p) { return p[1]; });
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    if (max === min) max = min + 1;
    var pad = 4, ih = h - pad * 2;
    var xStep = w / (d.length - 1);
    /* Build points */
    var pts = [];
    for (var i = 0; i < d.length; i++) {
      pts.push({ x: i * xStep, y: pad + ih - ((vals[i] - min) / (max - min)) * ih });
    }
    /* Area fill with bezier */
    ctx.beginPath();
    ctx.moveTo(0, h);
    ctx.lineTo(pts[0].x, pts[0].y);
    for (var i = 1; i < pts.length; i++) {
      var cpx = (pts[i - 1].x + pts[i].x) / 2;
      ctx.bezierCurveTo(cpx, pts[i - 1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
    }
    ctx.lineTo(w, h);
    ctx.closePath();
    var grad = ctx.createLinearGradient(0, 0, 0, h);
    grad.addColorStop(0, this.color + '25');
    grad.addColorStop(1, this.color + '02');
    ctx.fillStyle = grad;
    ctx.fill();
    /* Stroke with bezier */
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (var i = 1; i < pts.length; i++) {
      var cpx = (pts[i - 1].x + pts[i].x) / 2;
      ctx.bezierCurveTo(cpx, pts[i - 1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
    }
    ctx.strokeStyle = this.color;
    ctx.lineWidth = 1.5;
    ctx.stroke();
    /* End dot */
    var last = pts[pts.length - 1];
    ctx.beginPath();
    ctx.arc(last.x, last.y, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = this.color;
    ctx.fill();
    /* Y scale labels */
    ctx.fillStyle = '#2a2e48';
    ctx.font = '10px IBM Plex Mono, monospace';
    ctx.textAlign = 'left';
    ctx.fillText(max.toFixed(1), 3, pad + 10);
    ctx.fillText(min.toFixed(1), 3, h - 3);
  }
}

/* ── Init charts ── */
var charts = {
  latency: new Chart(document.getElementById('chart-latency'), '#3d80d0'),
  clients: new Chart(document.getElementById('chart-clients'), '#40b868'),
  interval: new Chart(document.getElementById('chart-interval'), '#9060c0'),
  throttle: new Chart(document.getElementById('chart-throttle'), '#e8a830')
};

var timeRange = 300;
var logFilter = 'all';
var lastLogTs = 0;
var allLogs = [];

/* ── Time range buttons ── */
document.querySelectorAll('.time-range button').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.time-range button').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    timeRange = parseInt(btn.dataset.range);
    fetchMetrics();
  });
});

/* ── Log filter buttons ── */
document.querySelectorAll('.log-filters button').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.log-filters button').forEach(function(b) { b.classList.remove('active'); });
    btn.classList.add('active');
    logFilter = btn.dataset.level;
    renderLogs();
  });
});

/* ── Helpers ── */
function fmtUp(s) {
  if (s < 60) return Math.floor(s) + 's';
  if (s < 3600) return Math.floor(s / 60) + 'm';
  var hh = Math.floor(s / 3600), mm = Math.floor((s % 3600) / 60);
  return hh + 'h ' + mm + 'm';
}
function fmtN(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return '' + n;
}
function fmtT(ts) {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function esc(s) { var d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

/* ── Fetch status for info bar ── */
function fetchStatus() {
  fetch('/transit-tracker/api/status').then(function(r) { return r.json(); }).then(function(s) {
    if (s.status === 'unavailable' || s.status === 'error') return;
    var prof = (s.config_path || '').split('/').pop() || '\u2014';
    document.getElementById('i-profile').textContent = prof;
    var key = s.oba_api_key || 'TEST';
    var keyEl = document.getElementById('i-apikey');
    keyEl.textContent = key;
    keyEl.className = 'val ' + (key === 'TEST' ? '' : 'green');
    document.getElementById('i-refresh').textContent = (s.refresh_interval || 30) + 's';
  }).catch(function() {});
}

/* ── Fetch metrics ── */
function fetchMetrics() {
  var since = timeRange > 0 ? (Date.now() / 1000 - timeRange) : 0;
  fetch('/transit-tracker/api/metrics?since=' + since).then(function(r) { return r.json(); }).then(function(m) {
    document.getElementById('live-dot').className = 'live-dot';
    document.getElementById('live-label').textContent = 'Live';

    document.getElementById('s-uptime').textContent = fmtUp(m.uptime_s);
    var t0 = new Date((m.ts - m.uptime_s) * 1000);
    document.getElementById('s-uptime-sub').textContent = 'since ' + t0.toLocaleTimeString();

    document.getElementById('s-clients').textContent = m.gauges.active_clients;
    document.getElementById('s-clients-sub').textContent = fmtN(m.counters.ws_connections) + ' total connections';

    document.getElementById('s-msgs').textContent = fmtN(m.counters.messages_sent);
    document.getElementById('s-msgs-sub').textContent = fmtN(m.counters.messages_received) + ' received';

    var ac = m.counters.api_calls;
    document.getElementById('s-api').textContent = fmtN(ac);
    var errR = ac > 0 ? (m.counters.api_errors / ac * 100).toFixed(1) : '0.0';
    document.getElementById('s-api-sub').textContent = errR + '% error rate';

    var thr = m.counters.throttle_events;
    var thrEl = document.getElementById('s-throttle');
    thrEl.textContent = fmtN(thr);
    thrEl.className = 'stat-value ' + (thr > 0 ? 'amber' : 'green');
    var thrR = ac > 0 ? (thr / ac * 100).toFixed(1) : '0.0';
    document.getElementById('s-throttle-sub').textContent = thrR + '% of calls';

    var errEl = document.getElementById('s-errors');
    errEl.textContent = fmtN(m.counters.api_errors);
    errEl.className = 'stat-value ' + (m.counters.api_errors > 0 ? 'red' : 'green');
    document.getElementById('s-errors-sub').textContent = fmtN(m.counters.api_errors) + ' total';

    charts.latency.setData(m.series.api_latency_ms);
    charts.clients.setData(m.series.active_clients);
    charts.interval.setData(m.series.refresh_interval_s);
    charts.throttle.setData(m.series.throttle_rate);

    function latest(arr) { return arr.length ? arr[arr.length - 1][1] : null; }
    var ll = latest(m.series.api_latency_ms);
    document.getElementById('cl-latency').textContent = ll !== null ? ll.toFixed(0) + ' ms' : '\u2014';
    var lc = latest(m.series.active_clients);
    document.getElementById('cl-clients').textContent = lc !== null ? Math.round(lc) + '' : '\u2014';
    var li = latest(m.series.refresh_interval_s);
    document.getElementById('cl-interval').textContent = li !== null ? li.toFixed(0) + 's' : '\u2014';
    var lt = latest(m.series.throttle_rate);
    document.getElementById('cl-throttle').textContent = lt !== null ? lt.toFixed(1) + '%' : '\u2014';
  }).catch(function() {
    document.getElementById('live-dot').className = 'live-dot off';
    document.getElementById('live-label').textContent = 'Offline';
  });
}

/* ── Fetch logs ── */
function fetchLogs() {
  fetch('/transit-tracker/api/logs?since=' + lastLogTs + '&limit=200').then(function(r) { return r.json(); }).then(function(data) {
    if (data.logs && data.logs.length) {
      allLogs = allLogs.concat(data.logs);
      if (allLogs.length > 500) allLogs = allLogs.slice(-500);
      lastLogTs = data.logs[data.logs.length - 1].ts + 0.001;
      renderLogs();
    }
  }).catch(function() {});
}

function renderLogs() {
  var list = document.getElementById('log-list');
  var f = allLogs;
  if (logFilter !== 'all') f = allLogs.filter(function(e) { return e.level === logFilter; });
  var vis = f.slice(-200);
  document.getElementById('log-count').textContent = '(' + allLogs.length + ')';
  list.innerHTML = vis.map(function(e) {
    var lv = e.level || 'INFO';
    var comp = e.component || e.logger || '';
    return '<div class="log-entry">' +
      '<span class="log-ts">' + fmtT(e.ts) + '</span>' +
      '<span class="log-level ' + lv + '">' + lv + '</span>' +
      '<span class="log-comp">' + esc(comp) + '</span>' +
      '<span class="log-msg">' + esc(e.msg || '') + '</span></div>';
  }).join('');
  list.scrollTop = list.scrollHeight;
}

/* ── Boot ── */
fetchStatus();
fetchMetrics();
fetchLogs();
setInterval(fetchStatus, 5000);
setInterval(fetchMetrics, 2000);
setInterval(fetchLogs, 2000);

})();
</script>
</body>
</html>
"""


def generate_simulator_html() -> str:
    """Generate a self-contained browser-based LED matrix simulator.

    Connects to the WebSocket server client-side, renders a pixel-perfect
    HUB75 LED matrix on an HTML5 Canvas, with MicroFont glyph data, realtime
    icon animation, and headsign scrolling.
    """
    from ..simulator import BaseSimulator, MicroFont

    # Export glyph data as JSON for the JS renderer
    glyphs_json = json.dumps({k: v for k, v in MicroFont.GLYPHS.items()})
    icon_json = json.dumps(MicroFont.REALTIME_ICON)

    # Build subscribe payload from current config so the web simulator
    # sends real subscription data instead of relying on server defaults.
    try:
        from ..config import TransitConfig, get_last_config_path, load_service_settings

        svc = load_service_settings()
        config_path = (
            get_last_config_path()
            or os.environ.get("CONFIG_PATH")
            or ("/config/config.yaml" if os.path.exists("/config/config.yaml") else None)
            or "config.yaml"
        )
        config = TransitConfig.load(config_path, service_settings=svc)

        class _Stub(BaseSimulator):
            async def run(self):
                pass

        stub = _Stub(config, force_live=True)
        sub_payload = stub.build_subscribe_payload(
            client_name="WebSimulator",
            limit=10,
        )
        subscribe_json = json.dumps(sub_payload)
    except Exception:
        subscribe_json = json.dumps({
            "event": "schedule:subscribe",
            "client_name": "WebSimulator",
            "data": {"routeStopPairs": "", "limit": 10},
        })

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Transit Tracker — LED Simulator</title>
<style>
:root {{
  --bg: #0a0a0a;
  --bg-card: #141418;
  --border: #252530;
  --text: #dde1ed;
  --text2: #8891b0;
  --muted: #505872;
  --green: #00c853;
  --red: #ff1744;
  --purple: #7c4dff;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'SF Mono','Menlo','Consolas',monospace; background: var(--bg); color: var(--text); }}
.header {{
  display: flex; align-items: center; justify-content: space-between;
  padding: 12px 24px; border-bottom: 1px solid var(--border); background: var(--bg-card);
}}
.header h1 {{ font-size: 14px; font-weight: 600; }}
.header h1 span {{ color: var(--purple); }}
.controls {{
  display: flex; align-items: center; gap: 16px; font-size: 12px; color: var(--text2);
}}
.controls select, .controls input {{
  background: #1a1a24; border: 1px solid var(--border); color: var(--text);
  padding: 4px 8px; border-radius: 4px; font-family: inherit; font-size: 12px;
}}
.status-dot {{
  display: inline-block; width: 8px; height: 8px; border-radius: 50%;
  background: var(--red); transition: background 0.3s;
}}
.status-dot.connected {{ background: var(--green); animation: pulse 2s infinite; }}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
.sim-container {{
  display: flex; flex-direction: column; align-items: center; justify-content: center;
  min-height: calc(100vh - 49px); padding: 40px;
}}
canvas {{
  image-rendering: pixelated; border: 2px solid #333;
  border-radius: 4px; background: #000;
}}
.info {{
  margin-top: 16px; font-size: 11px; color: var(--muted); text-align: center;
}}
a {{ color: var(--purple); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
/* Embed mode: canvas fills entire viewport, no chrome */
body.embed {{ background: #000; margin: 0; padding: 0; overflow: hidden; }}
body.embed .header {{ display: none; }}
body.embed .sim-container {{ min-height: 0; height: 100vh; padding: 0; display: block; }}
body.embed .info {{ display: none; }}
body.embed canvas {{ border: none; border-radius: 0; display: block; width: 100%; height: 100%; }}
</style>
</head>
<body>
<script>if(location.search.indexOf('embed=1')>=0)document.body.classList.add('embed');</script>
<div class="header">
  <h1><span>Transit Tracker</span> &mdash; LED Simulator</h1>
  <div class="controls">
    <span><span class="status-dot" id="ws-dot"></span> <span id="ws-label">Disconnected</span></span>
    <label>Endpoint:
      <select id="endpoint-select">
        <option value="local">Local (via /transit-tracker/ws proxy)</option>
        <option value="cloud">Cloud (tt.horner.tj)</option>
        <option value="custom">Custom...</option>
      </select>
    </label>
    <input type="text" id="custom-url" placeholder="ws://host:port" style="display:none;width:200px;">
    <a href="/transit-tracker/monitor">Monitor &rarr;</a>
  </div>
</div>
<div class="sim-container">
  <canvas id="led-canvas"></canvas>
  <div class="info">
    <div id="trip-info">Waiting for data...</div>
    <div style="margin-top:8px;">Pixel scale: <input type="range" id="scale-range" min="2" max="12" value="6" style="vertical-align:middle;width:100px;">
    <span id="scale-label">6x</span></div>
  </div>
</div>

<script>
// ---- Config ----
const PANEL_W = 64, PANEL_H = 32, NUM_PANELS = 2;
const DISPLAY_W = PANEL_W * NUM_PANELS;
const DISPLAY_H = PANEL_H;
let PIXEL_SCALE = (location.search.indexOf('embed=1') >= 0) ? 3 : 6;
const PIXEL_GAP = 1;

// ---- Glyph data from Python MicroFont ----
const GLYPHS = {glyphs_json};
const REALTIME_ICON = {icon_json};

// ---- Subscribe payload from config ----
const SUBSCRIBE_PAYLOAD = {subscribe_json};

// ---- State ----
let ws = null;
let trips = [];
let startTime = Date.now();
let subscribePayload = null;

// ---- Canvas setup ----
const canvas = document.getElementById('led-canvas');
const ctx = canvas.getContext('2d');

function resizeCanvas() {{
  canvas.width = DISPLAY_W * PIXEL_SCALE;
  canvas.height = DISPLAY_H * PIXEL_SCALE;
}}
resizeCanvas();

// ---- Scale slider ----
const scaleRange = document.getElementById('scale-range');
const scaleLabel = document.getElementById('scale-label');
scaleRange.addEventListener('input', () => {{
  PIXEL_SCALE = parseInt(scaleRange.value);
  scaleLabel.textContent = PIXEL_SCALE + 'x';
  resizeCanvas();
}});

// ---- Font rendering ----
function getGlyphBitmap(ch) {{
  const key = ch.toUpperCase();
  const glyph = GLYPHS[key] || GLYPHS[ch] || GLYPHS['?'];
  if (!glyph) return Array.from({{length:7}}, () => []);
  const rows = [];
  for (let i = 0; i < 7; i++) {{
    const bits = glyph[i];
    const row = [];
    for (let b = 4; b >= 0; b--) {{
      row.push((bits >> b) & 1);
    }}
    row.push(0); // gap
    rows.push(row);
  }}
  return rows;
}}

function getTextBitmap(text) {{
  const rows = Array.from({{length:7}}, () => []);
  for (const ch of text) {{
    const glyph = getGlyphBitmap(ch);
    for (let i = 0; i < 7; i++) {{
      rows[i].push(...glyph[i]);
    }}
  }}
  return rows;
}}

function getIconFrame(elapsed) {{
  const cycleMs = Math.floor(elapsed * 1000) % 4000;
  let frame = 0;
  if (cycleMs >= 3000) {{
    frame = Math.min(Math.floor((cycleMs - 3000) / 200) + 1, 5);
  }}
  const rows = Array.from({{length:7}}, () => []);
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

// ---- Color helpers ----
function parseColor(c) {{
  if (!c) return '#cccc00';
  if (c === 'hot_pink') return '#ff69b4';
  if (c === 'yellow') return '#cccc00';
  if (c === 'white') return '#ffffff';
  if (c === 'bright_blue') return '#5599ff';
  if (c === 'grey74') return '#bbbbbb';
  if (c.startsWith('#')) return c;
  return '#cccc00';
}}

function dimColor(c) {{
  // Return a very dim version for the "dim segment" of the icon
  return '#223366';
}}

// ---- Trip processing (mirrors BaseSimulator._process_trip) ----
function processDepartures(rawTrips) {{
  const now = Date.now();
  const deps = [];
  for (const trip of rawTrips) {{
    const tripId = trip.tripId;
    if (!tripId) continue;
    let arrVal = trip.arrivalTime || trip.predictedArrivalTime || 0;
    if (!arrVal) continue;
    const baseMs = arrVal > 1e12 ? arrVal : arrVal * 1000;
    const diffMin = Math.floor((baseMs - now) / 60000);
    if (diffMin < -1) continue;

    const routeName = trip.routeName || '?';
    const headsign = trip.headsign || 'Transit';
    const isLive = !!trip.isRealtime;
    const colorHex = trip.routeColor;
    let color = 'yellow';
    if (routeName.includes('14')) color = 'hot_pink';
    else if (colorHex) color = '#' + colorHex;

    deps.push({{
      trip_id: tripId,
      diff: Math.max(0, diffMin),
      route: routeName,
      headsign: headsign,
      color: color,
      live: isLive,
      stop_id: trip.stopId || '',
    }});
  }}
  deps.sort((a,b) => a.diff - b.diff);
  // Diversity cap: 1 per stop, then fill to 3
  const final = [];
  const seen = new Set();
  for (const d of deps) {{
    if (!seen.has(d.stop_id)) {{ final.push(d); seen.add(d.stop_id); }}
    if (final.length >= 3) break;
  }}
  if (final.length < 3) {{
    for (const d of deps) {{
      if (!final.includes(d)) final.push(d);
      if (final.length >= 3) break;
    }}
  }}
  final.sort((a,b) => a.diff - b.diff);
  return final;
}}

// ---- Render frame ----
function renderFrame() {{
  const elapsed = (Date.now() - startTime) / 1000;
  ctx.fillStyle = '#000000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const deps = processDepartures(trips);

  if (deps.length === 0) {{
    const msg = trips.length === 0 ? 'Connecting...' : 'No Live Buses';
    const bm = getTextBitmap(msg);
    for (let r = 0; r < 7; r++) {{
      for (let c = 0; c < bm[r].length; c++) {{
        if (bm[r][c]) {{
          ctx.fillStyle = '#00cccc';
          ctx.fillRect(c * PIXEL_SCALE, r * PIXEL_SCALE,
                       PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
        }}
      }}
    }}
    requestAnimationFrame(renderFrame);
    return;
  }}

  let rowY = 0;
  for (let di = 0; di < Math.min(deps.length, 3); di++) {{
    const dep = deps[di];
    const timeStr = dep.diff <= 0 ? 'Now' : dep.diff + 'm';
    const routeColor = parseColor(dep.color);
    const timeColor = dep.live ? '#5599ff' : '#bbbbbb';

    // Compute segment widths
    const routeBm = getTextBitmap(dep.route + '  ');
    const headsignBm = getTextBitmap(dep.headsign);
    const timeBm = getTextBitmap(' ' + timeStr);
    const iconW = dep.live ? 8 : 0;
    const fixedW = routeBm[0].length + timeBm[0].length + iconW;
    const headsignAreaW = Math.max(0, DISPLAY_W - fixedW);

    let x = 0;
    // Route
    for (let r = 0; r < 7; r++) {{
      for (let c = 0; c < routeBm[r].length; c++) {{
        if (routeBm[r][c]) {{
          ctx.fillStyle = routeColor;
          ctx.fillRect((x + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                       PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
        }}
      }}
    }}
    x += routeBm[0].length;

    // Headsign (clipped to area)
    for (let r = 0; r < 7; r++) {{
      for (let c = 0; c < Math.min(headsignBm[r].length, headsignAreaW); c++) {{
        if (headsignBm[r][c]) {{
          ctx.fillStyle = '#ffffff';
          ctx.fillRect((x + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                       PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
        }}
      }}
    }}
    x += headsignAreaW;

    // Live icon
    if (dep.live) {{
      const icon = getIconFrame(elapsed);
      const ix = x + 1;
      for (let r = 0; r < 6; r++) {{
        for (let c = 0; c < 6; c++) {{
          const val = icon[r][c];
          if (val === 2) {{
            ctx.fillStyle = '#ffffff';
            ctx.fillRect((ix + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                         PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
          }} else if (val === 1) {{
            ctx.fillStyle = dimColor();
            ctx.fillRect((ix + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                         PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
          }}
        }}
      }}
      x += 8;
    }}

    // Time
    for (let r = 0; r < 7; r++) {{
      for (let c = 0; c < timeBm[r].length; c++) {{
        if (timeBm[r][c]) {{
          ctx.fillStyle = timeColor;
          ctx.fillRect((x + c) * PIXEL_SCALE, (rowY + r) * PIXEL_SCALE,
                       PIXEL_SCALE - PIXEL_GAP, PIXEL_SCALE - PIXEL_GAP);
        }}
      }}
    }}

    rowY += 11; // 7 rows + 4 spacer
  }}

  // Update info text
  const infoEl = document.getElementById('trip-info');
  const lines = deps.map(d => d.route + '  ' + d.headsign + '  ' + (d.live ? '\\u25c9' : '\\u25cb') + ' ' + (d.diff <= 0 ? 'Now' : d.diff + 'm'));
  infoEl.textContent = lines.join('  |  ');

  requestAnimationFrame(renderFrame);
}}

// ---- WebSocket ----
function getWsUrl() {{
  const sel = document.getElementById('endpoint-select').value;
  const custom = document.getElementById('custom-url');
  const wsProt = location.protocol === 'https:' ? 'wss://' : 'ws://';
  // Connect via /transit-tracker/ws on the same origin — the web server
  // proxies to the internal WS server on :8000.
  if (sel === 'local') return wsProt + location.host + '/transit-tracker/ws';
  if (sel === 'cloud') return 'wss://tt.horner.tj/';
  return custom.value || (wsProt + location.host + '/transit-tracker/ws');
}}

function connect() {{
  const url = getWsUrl();
  const dot = document.getElementById('ws-dot');
  const label = document.getElementById('ws-label');

  if (ws) {{ try {{ ws.close(); }} catch(e) {{}} }}
  label.textContent = 'Connecting...';
  dot.className = 'status-dot';

  try {{
    ws = new WebSocket(url);
  }} catch(e) {{
    label.textContent = 'Error: ' + e.message;
    setTimeout(connect, 5000);
    return;
  }}

  ws.onopen = () => {{
    dot.className = 'status-dot connected';
    label.textContent = 'Connected to ' + url.replace('wss://', '').replace('ws://', '').split('/')[0];
    ws.send(JSON.stringify(SUBSCRIBE_PAYLOAD));
  }};

  ws.onmessage = (ev) => {{
    try {{
      const msg = JSON.parse(ev.data);
      if (msg.event === 'schedule') {{
        trips = (msg.data || {{}}).trips || [];
      }}
    }} catch(e) {{}}
  }};

  ws.onclose = () => {{
    dot.className = 'status-dot';
    label.textContent = 'Disconnected';
    setTimeout(connect, 3000);
  }};

  ws.onerror = () => {{
    dot.className = 'status-dot';
    label.textContent = 'Connection error';
  }};
}}

// ---- Endpoint selector ----
document.getElementById('endpoint-select').addEventListener('change', (e) => {{
  const custom = document.getElementById('custom-url');
  custom.style.display = e.target.value === 'custom' ? 'inline-block' : 'none';
  connect();
}});
document.getElementById('custom-url').addEventListener('change', connect);

// ---- Start ----
connect();
requestAnimationFrame(renderFrame);
</script>
</body>
</html>"""
