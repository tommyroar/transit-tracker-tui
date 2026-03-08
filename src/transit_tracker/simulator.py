import asyncio
import json
import os
import sys
import time
import websockets
from datetime import datetime, timezone
from bdfparser import Font
from rich.live import Live
from rich.text import Text
from rich.panel import Panel
from rich.console import Console, Group
from typing import Optional

from .config import TransitConfig

class LEDSimulator:
    def __init__(self, config: TransitConfig, force_live: bool = True):
        # VERSION: 2026-03-08-WEBSOCKET-FIXED
        self.config = config
        self.force_live = force_live
        self.state = {} # stopId -> { 'trips': [], 'timestamp': float }
        self.running = True
        self.start_time = time.time()

        # Initialize mock state immediately if present and not forcing live
        if not self.force_live and (self.config.mock_state or self.config.captures):
            mock_data = self.config.mock_state
            if not mock_data and self.config.captures:
                # Parse the latest capture on the fly
                latest = self.config.captures[-1]
                display_text = latest.get("display", "").strip()
                mock_data = []
                for line in display_text.split('\n'):
                    # Simple parser for capture lines
                    parts = line.split()
                    if not parts: continue
                    route = parts[0]
                    live = "{LIVE}" in line
                    time_str = parts[-1].replace("{LIVE}", "").replace("m", "")
                    try:
                        diff = int(time_str)
                    except ValueError:
                        diff = 0
                    headsign = " ".join(line.replace("{LIVE}", " ").split()[1:-1])
                    mock_data.append({
                        "route": route, "headsign": headsign, "diff": diff, 
                        "live": live, "color": "cyan" if route == "14" else "yellow"
                    })
            self.state = {"mock": {"trips": mock_data, "timestamp": time.time()}}

        # Load a tiny bitmap font that mimics LED displays
        font_path = os.path.join(os.path.dirname(__file__), "fonts", "tom-thumb.bdf")
        if not os.path.exists(font_path):
            self.font = None
        else:
            self.font = Font(font_path)

    async def _listen_websocket(self):
        if not self.force_live and "mock" in self.state:
            return

        # Strip trailing slash from API URL if present
        api_url = self.config.api_url.rstrip("/")
        while self.running:
            try:
                async with websockets.connect(api_url) as ws:
                    # Construct routeStopPairs string: feed:route,feed:stop;...
                    pairs = []
                    for sub in self.config.subscriptions:
                        # The API expects feedId:routeId,feedId:stopId
                        r_id = sub.route if ":" in sub.route else f"{sub.feed}:{sub.route}"
                        s_id = sub.stop if ":" in sub.stop else f"{sub.feed}:{sub.stop}"
                        pairs.append(f"{r_id},{s_id}")
                    
                    pairs_str = ";".join(pairs)
                    
                    sub_payload = {
                        "event": "schedule:subscribe",
                        "data": {
                            "routeStopPairs": pairs_str,
                            "limit": 5
                        }
                    }
                    await ws.send(json.dumps(sub_payload))

                    async for message in ws:
                        if not self.running:
                            break
                        data = json.loads(message)
                        if data.get("event") == "schedule":
                            # The TJ Horner API returns all subscribed trips in one update
                            d = data.get("data", {})
                            self.state["live"] = {
                                "trips": d.get("trips", []),
                                "timestamp": time.time()
                            }
            except Exception:
                if self.running:
                    await asyncio.sleep(5) # Retry on connection loss

    def _render_led_string(self, text: str, color: str = "yellow", force_upper: bool = False) -> Text:
        """Renders text as a dot-matrix style LED string using the bitmap font."""
        if not self.font:
            return Text(text, style=color, no_wrap=True)
            
        render_text = text.upper() if force_upper else text
        canvas = self.font.draw(render_text, mode=1)
        pixels = canvas.todata(2)
        
        rich_text = Text(no_wrap=True)
        for row in pixels:
            for pixel in row:
                if pixel:
                    rich_text.append("●", style=f"bold {color}")
                else:
                    rich_text.append("·", style="dim black")
            rich_text.append("\n")
        return rich_text

    def _generate_frame(self, reference_time: Optional[datetime] = None) -> Panel:
        all_departures = []
        now = reference_time or datetime.now(timezone.utc)
        # API timestamps are UTC milliseconds
        current_time_ms = int(now.timestamp() * 1000)
        now_ts = now.timestamp()
        
        elapsed = 0 if reference_time else (time.time() - self.start_time)

        # MOCK STATE HANDLING
        is_mock = "mock" in self.state
        if is_mock:
            mock_data = self.state["mock"]["trips"]
            for mock_bus in mock_data:
                all_departures.append({
                    "diff": mock_bus.get("diff", 0),
                    "route": mock_bus.get("route", "??"),
                    "headsign": mock_bus.get("headsign", "Mock Data"),
                    "color": mock_bus.get("color", "yellow"),
                    "live": mock_bus.get("live", False)
                })
        else:
            live_data = self.state.get("live")
            if live_data and (now_ts - live_data.get("timestamp", 0) <= 300):
                for trip in live_data.get("trips", []):
                    # Filter: Only show trips that match one of our subscriptions
                    trip_route_id = trip.get("routeId", "")
                    trip_stop_id = trip.get("stopId", "")
                    
                    # Find subscription (robust match for prefixed/unprefixed)
                    sub = None
                    for s in self.config.subscriptions:
                        if (s.route == trip_route_id or s.route.split(":")[-1] == trip_route_id or (trip_route_id and trip_route_id.split(":")[-1] == s.route)) and \
                           (s.stop == trip_stop_id or s.stop.split(":")[-1] == trip_stop_id or (trip_stop_id and trip_stop_id.split(":")[-1] == s.stop)):
                            sub = s
                            break
                    
                    if not sub:
                        continue

                    trip_id = trip.get("tripId")
                    if not trip_id: continue
                    
                    # Dedup trips
                    if any(d.get("trip_id") == trip_id for d in all_departures):
                        continue

                    # Arrival from confirmation: 'arrivalTime' is unix seconds
                    # Also support 'predictedArrivalTime' / 'scheduledArrivalTime' (ISO or ms) for legacy
                    arr_val = trip.get("arrivalTime") or trip.get("predictedArrivalTime") or trip.get("scheduledArrivalTime")
                    if not arr_val: continue
                    
                    if isinstance(arr_val, str):
                        try:
                            dt = datetime.fromisoformat(arr_val.replace("Z", "+00:00"))
                            arr_time_ms = int(dt.timestamp() * 1000)
                        except ValueError: continue
                    elif arr_val > 10**12: # Likely milliseconds (e.g. 1773012765000)
                        arr_time_ms = arr_val
                    else: # Likely seconds (e.g. 1773012765)
                        arr_time_ms = arr_val * 1000

                    offset_sec = 0
                    if sub and sub.time_offset:
                        try:
                            clean_offset = sub.time_offset.lower().replace("min", "").strip()
                            offset_sec = int(clean_offset) * 60 if "min" in sub.time_offset.lower() else int(clean_offset)
                        except ValueError: pass

                    raw_diff_sec = (arr_time_ms - current_time_ms) / 1000.0
                    eff_diff_sec = raw_diff_sec + offset_sec
                    
                    raw_mins = int(raw_diff_sec / 60)
                    eff_mins = int(eff_diff_sec / 60)

                    # Filter: Use raw_mins to match hardware behavior
                    if self.config.display_offset:
                        display_mins = eff_mins
                        should_show = eff_mins >= 0
                    else:
                        display_mins = raw_mins if self.config.time_display == "arrival" else eff_mins
                        should_show = raw_mins >= -2

                    if should_show:
                        route_name = trip.get("routeName") or trip.get("routeShortName")
                        if not route_name:
                            route_name = sub.label.split("-")[0].strip().split()[0] if sub and sub.label else sub.route.split("_")[-1]
                            
                        headsign = trip.get("headsign") or trip.get("tripHeadsign")
                        if not headsign and sub:
                            headsign = sub.label.split("-")[-1].strip() if "-" in sub.label else sub.label
                            
                        is_live = trip.get("isRealtime", "predictedArrivalTime" in trip)
                        
                        color_hex = trip.get("routeColor")
                        color = f"#{color_hex}" if color_hex else ("cyan" if route_name == "14" else "yellow")

                        if display_mins < 0:
                            display_mins = 0

                        all_departures.append({
                            "trip_id": trip_id,
                            "diff": display_mins, 
                            "route": route_name, 
                            "headsign": headsign or "Transit",
                            "color": color,
                            "live": is_live
                        })

        all_departures.sort(key=lambda x: x["diff"])
        
        lines = []
        if not self.state:
            lines.append(self._render_led_string("Connecting...", color="cyan"))
        elif not all_departures:
            msg = "No Mock Buses" if is_mock else "No Live Buses"
            lines.append(self._render_led_string(msg, color="white"))
        else:
            char_width = 16 * self.config.num_panels
            for dep in all_departures[:3]: 
                icon = "*" if dep["live"] else " "
                eta_str = f"{dep['diff']}m"
                full_eta_part = f"{icon}{eta_str}"
                
                r_str = f"{str(dep['route'])[:3]:>3}"
                fixed_len = 3 + 1 + 1 + len(full_eta_part)
                max_h = char_width - fixed_len
                
                headsign = dep['headsign']
                if self.config.scroll_headsigns and len(headsign) > max_h:
                    display_text = headsign + "    "
                    shift = int(elapsed * 2) % len(display_text)
                    h_text = (display_text[shift:] + display_text[:shift])[:max_h]
                else:
                    h_text = headsign[:max_h]
                
                line_str = f"{r_str} {h_text:<{max_h}} {full_eta_part}"
                lines.append(self._render_led_string(line_str, color=dep["color"]))

        panel_title = f"[bold red]HUB75 {64 * self.config.num_panels}x32 LED SIMULATOR[/bold red]"
        if is_mock:
            panel_title += " [yellow](MOCK DATA)[/yellow]"
        else:
            panel_title += " [green](LIVE)[/green]"

        return Panel(
            Group(*lines),
            title=panel_title,
            subtitle="[dim]Ctrl+C to Exit[/dim]",
            border_style="red",
            style="on black",
            padding=(0, 1),
            expand=False
        )

    async def run(self):
        ws_task = asyncio.create_task(self._listen_websocket())
        try:
            with Live(self._generate_frame(), refresh_per_second=4, screen=True) as live:
                while True:
                    await asyncio.sleep(0.25)
                    live.update(self._generate_frame())
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass

def run_simulator(config: TransitConfig, force_live: bool = False):
    if not config.subscriptions and not config.mock_state and not config.captures:
        Console().print("[bold red]Error:[/bold red] No stops or mock state/captures configured.")
        return
    sim = LEDSimulator(config, force_live=force_live)
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        pass
