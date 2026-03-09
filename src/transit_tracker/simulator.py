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
from typing import Optional, Union

from .config import TransitConfig

class MicroFont:
    """A minimal 5x7 proportional-style font implementation for exact LED simulation."""
    # 5x7 glyph data (each list is 7 rows, each row is a 5-bit integer)
    GLYPHS = {
        '0': [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        '1': [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
        '2': [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
        '3': [0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E],
        '4': [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
        '5': [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
        '6': [0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E],
        '7': [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
        '8': [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
        '9': [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
        'A': [0x04, 0x0A, 0x11, 0x11, 0x1F, 0x11, 0x11],
        'B': [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
        'C': [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
        'D': [0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C],
        'E': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
        'F': [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
        'G': [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
        'H': [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        'I': [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
        'J': [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
        'K': [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
        'L': [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
        'M': [0x11, 0x1B, 0x15, 0x11, 0x11, 0x11, 0x11],
        'N': [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
        'O': [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        'P': [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
        'Q': [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
        'R': [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
        'S': [0x0E, 0x11, 0x10, 0x0E, 0x01, 0x11, 0x0E],
        'T': [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
        'U': [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        'V': [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04],
        'W': [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
        'X': [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
        'Y': [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
        'Z': [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
        ' ': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        'm': [0x00, 0x00, 0x1A, 0x15, 0x15, 0x15, 0x15],
        '*': [ # Default/Full LIVE icon
            [0x04, 0x15, 0x0E, 0x1F, 0x0E, 0x15, 0x04]
        ],
        '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04],
        '-': [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
        '?': [0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04],
    }

    # Animated LIVE icon frames
    LIVE_FRAMES = [
        [0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], # Dot
        [0x04, 0x0A, 0x00, 0x00, 0x00, 0x00, 0x00], # Expanding 1
        [0x04, 0x0A, 0x11, 0x00, 0x00, 0x00, 0x00], # Expanding 2
        [0x04, 0x0A, 0x11, 0x00, 0x00, 0x00, 0x00], # Hold
        [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00], # Blink out
    ]

    @classmethod
    def get_bitmap(cls, text: str, elapsed: float = 0.0) -> list[list[int]]:
        rows = [[] for _ in range(7)]
        # Split text into segments to handle '*' animation
        segments = []
        curr = ""
        for char in text:
            if char == '*':
                if curr: segments.append(curr)
                segments.append('*')
                curr = ""
            else:
                curr += char
        if curr: segments.append(curr)

        for seg in segments:
            if seg == '*':
                # Animate LIVE icon: 5 frames, 0.15s each
                idx = int(elapsed * 6.6) % len(cls.LIVE_FRAMES)
                glyph = cls.LIVE_FRAMES[idx]
            else:
                for char in seg:
                    glyph = cls.GLYPHS.get(char.upper(), cls.GLYPHS['?'])
                    # If it's a list of lists (multiple frames), take first
                    if isinstance(glyph[0], list):
                        glyph = glyph[0]
                    
                    for i in range(7):
                        bits = glyph[i]
                        for b in range(4, -1, -1):
                            rows[i].append(1 if (bits & (1 << b)) else 0)
                        rows[i].append(0) # Gap
                continue

            # Append the animated glyph to rows
            for i in range(7):
                bits = glyph[i]
                for b in range(4, -1, -1):
                    rows[i].append(1 if (bits & (1 << b)) else 0)
                rows[i].append(0) # Gap
        return rows

class LEDSimulator:
    def __init__(self, config: TransitConfig, force_live: bool = True):
        # VERSION: 2026-03-08-ANIMATED
        self.config = config
        self.force_live = force_live
        self.state = {} # stopId -> { 'trips': [], 'timestamp': float }
        self.running = True
        self.start_time = time.time()
        self.microfont = MicroFont()

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
                        "live": live, "color": "hot_pink" if route == "14" else "yellow"
                    })
            self.state = {"mock": {"trips": mock_data, "timestamp": time.time()}}

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

    def _render_led_string(self, text_or_spans: Union[str, list[tuple[str, str]]], color: str = "yellow", elapsed: float = 0.0) -> Text:
        """Renders text as a dot-matrix style LED string using the MicroFont."""
        if isinstance(text_or_spans, str):
            spans = [(text_or_spans, color)]
        else:
            spans = text_or_spans

        all_pixel_spans = []
        for t, c in spans:
            bitmap = self.microfont.get_bitmap(t, elapsed=elapsed)
            all_pixel_spans.append((bitmap, c))

        if not all_pixel_spans:
            return Text()

        rich_text = Text(no_wrap=True)
        for r in range(7): # 7 rows for 5x7 font
            for bitmap, c in all_pixel_spans:
                row = bitmap[r]
                for pixel in row:
                    if pixel:
                        rich_text.append("●", style=f"bold {c}")
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
        
        real_elapsed = time.time() - self.start_time
        elapsed = 0 if reference_time else real_elapsed

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
                        route_name = str(trip.get("routeName") or trip.get("routeShortName") or "")
                        if not route_name:
                            route_name = sub.label.split("-")[0].strip().split()[0] if sub and sub.label else sub.route.split("_")[-1]
                            
                        headsign = trip.get("headsign") or trip.get("tripHeadsign")
                        if not headsign and sub:
                            headsign = sub.label.split("-")[-1].strip() if "-" in sub.label else sub.label
                            
                        is_live = trip.get("isRealtime", "predictedArrivalTime" in trip)
                        
                        color_hex = trip.get("routeColor")
                        # Match IMG_3077: route 14 is hot_pink
                        if "14" in route_name:
                            color = "hot_pink"
                        elif color_hex:
                            color = f"#{color_hex}"
                        else:
                            color = "yellow"

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
            lines.append(self._render_led_string("Connecting...", color="cyan", elapsed=elapsed))
        elif not all_departures:
            msg = "No Mock Buses" if is_mock else "No Live Buses"
            lines.append(self._render_led_string(msg, color="white", elapsed=elapsed))
        else:
            # 64px per panel / 6px per char (5px glyph + 1px gap) = ~10.6 chars per panel.
            char_width = int(64 * self.config.num_panels / 6)
            for dep in all_departures[:3]: 
                icon = "*" if dep["live"] else " "
                eta_str = f"{dep['diff']}m"
                full_eta_part = f"{icon}{eta_str}"
                
                r_str = f"{str(dep['route'])[:3]:<3}"
                fixed_len = 3 + 1 + 1 + len(full_eta_part)
                max_h_len = char_width - fixed_len
                
                headsign = dep['headsign']
                if self.config.scroll_headsigns and len(headsign) > max_h_len:
                    # Ping-pong scroll
                    overflow = len(headsign) - max_h_len
                    scroll_speed = 0.2  # Seconds per character
                    scroll_duration = overflow * scroll_speed
                    wait_duration = 2.0
                    total_cycle = (wait_duration + scroll_duration) * 2
                    
                    cycle_pos = elapsed % total_cycle
                    
                    if cycle_pos < wait_duration:
                        shift = 0
                    elif cycle_pos < (wait_duration + scroll_duration):
                        progress = (cycle_pos - wait_duration) / scroll_duration
                        shift = int(progress * overflow)
                    elif cycle_pos < (wait_duration * 2 + scroll_duration):
                        shift = overflow
                    else:
                        progress = (cycle_pos - (wait_duration * 2 + scroll_duration)) / scroll_duration
                        shift = overflow - int(progress * overflow)
                        
                    h_text = headsign[shift : shift + max_h_len]
                else:
                    h_text = headsign[:max_h_len]
                
                spans = [
                    (f"{r_str} ", dep["color"]),
                    (f"{h_text:<{max_h_len}} ", "white"),
                    (full_eta_part, "bright_blue")
                ]
                lines.append(self._render_led_string(spans, elapsed=elapsed))

        panel_title = f"[bold red]HUB75 {64 * self.config.num_panels}x32 LED SIMULATOR[/bold red]"
        if is_mock:
            panel_title += " [yellow](MOCK DATA)[/yellow]"
        else:
            source = "LOCAL" if "localhost" in self.config.api_url or "127.0.0.1" in self.config.api_url else self.config.api_url.replace("wss://", "").replace("ws://", "").split('/')[0]
            panel_title += f" [green](LIVE from {source})[/green]"

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
            with Live(self._generate_frame(), refresh_per_second=10, screen=True) as live:
                while True:
                    await asyncio.sleep(0.1)
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
