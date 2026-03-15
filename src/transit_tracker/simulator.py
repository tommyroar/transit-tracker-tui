import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

import websockets
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

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
        '6': [0x0E, 0x10, 0x10, 0x1E, 0x11, 0x11, 0x0E],
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
        '.': [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04],
        '-': [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
        '>': [0x10, 0x08, 0x04, 0x08, 0x10, 0x00, 0x00],
        '(': [0x04, 0x08, 0x08, 0x08, 0x08, 0x08, 0x04],
        ')': [0x08, 0x04, 0x04, 0x04, 0x04, 0x04, 0x08],
        '/': [0x01, 0x02, 0x04, 0x08, 0x10, 0x00, 0x00],
        '?': [0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04],
    }

    # Animated LIVE icon from esphome-transit-tracker
    REALTIME_ICON = [
        [0, 0, 0, 3, 3, 3],
        [0, 0, 3, 0, 0, 0],
        [0, 3, 0, 0, 2, 2],
        [3, 0, 0, 2, 0, 0],
        [3, 0, 2, 0, 0, 1],
        [3, 0, 2, 0, 1, 1]
    ]

    _bdf_font = None
    _bdf_loaded = False

    @classmethod
    def _get_bdf(cls):
        if not cls._bdf_loaded:
            cls._bdf_loaded = True
            font_path = os.path.join(os.path.dirname(__file__), "fonts", "tom-thumb.bdf")
            if os.path.exists(font_path):
                try:
                    from bdfparser import Font
                    cls._bdf_font = Font(font_path)
                except ImportError:
                    pass
        return cls._bdf_font

    @classmethod
    def get_bitmap(cls, text: str) -> list[list[int]]:
        """Returns a non-animated bitmap for text."""
        bdf = cls._get_bdf()
        if bdf and text:
            drawn = bdf.draw(text)
            lines = drawn.todata(2)
            rows = []
            for i in range(7):
                if i < len(lines):
                    rows.append([int(c) for c in lines[i]])
                else:
                    rows.append([0] * (len(lines[0]) if lines else 0))
            return rows

        rows = [[] for _ in range(7)]
        for char in text:
            glyph = cls.GLYPHS.get(char.upper(), cls.GLYPHS['?'])
            for i in range(7):
                bits = glyph[i]
                for b in range(4, -1, -1):
                    rows[i].append(1 if (bits & (1 << b)) else 0)
                rows[i].append(0) # Gap
        return rows

    @classmethod
    def get_live_icon_frame(cls, elapsed: float) -> list[list[int]]:
        """Calculates the current frame of the 6x6 realtime icon.
        Returns 0 for transparent, 1 for dim segment, 2 for lit segment.
        """
        # 4000ms cycle: 3000ms idle, then 5 frames of 200ms
        cycle_ms = int(elapsed * 1000) % 4000
        frame = 0
        if cycle_ms >= 3000:
            frame = (cycle_ms - 3000) // 200 + 1
            if frame > 5: frame = 5

        rows = [[] for _ in range(7)]
        for r in range(6):
            for c in range(6):
                seg = cls.REALTIME_ICON[r][c]
                if seg == 0:
                    rows[r].append(0) # Transparent
                    continue
                
                is_lit = False
                if seg == 1 and frame in [1, 2, 3]: is_lit = True
                elif seg == 2 and frame in [2, 3, 4]: is_lit = True
                elif seg == 3 and frame in [3, 4, 5]: is_lit = True
                
                rows[r].append(2 if is_lit else 1)
        # Pad 7th row
        rows[6] = [0] * 6
        return rows

class LEDSimulator:
    def __init__(self, config: TransitConfig, force_live: bool = True):
        # VERSION: 2026-03-08-REPRODUCED-HARDWARE
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

        # Correctly prioritize the local API if configured
        api_url = self.config.api_url
        if self.config.use_local_api and ("localhost" not in api_url and "127.0.0.1" not in api_url):
            # Fallback/Safety: if use_local_api is True but url isn't local, force it
            api_url = "ws://localhost:8000"
        
        api_url = api_url.rstrip("/")
        
        while self.running:
            try:
                async with websockets.connect(api_url) as ws:
                    # Construct routeStopPairs string: feed:route,feed:stop,offset;...
                    pairs = []
                    for sub in self.config.subscriptions:
                        # The API expects feedId:routeId,feedId:stopId[,offsetInSeconds]
                        r_id = sub.route if ":" in sub.route else f"{sub.feed}:{sub.route}"
                        s_id = sub.stop if ":" in sub.stop else f"{sub.feed}:{sub.stop}"
                        
                        # Convert "-7min" or "5m" to seconds
                        off_sec = 0
                        try:
                            match = re.search(r"(-?\d+)", str(sub.time_offset))
                            if match:
                                off_sec = int(match.group(1)) * 60
                        except:
                            pass
                            
                        pairs.append(f"{r_id},{s_id},{off_sec}")
                    
                    pairs_str = ";".join(pairs)
                    
                    sub_payload = {
                        "event": "schedule:subscribe",
                        "client_name": "Simulator",
                        "data": {
                            "routeStopPairs": pairs_str,
                            "limit": 10 # Increase limit to allow for offset-reordering
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

    def _render_trip_row(self, dep: dict, elapsed: float) -> list[Text]:
        """Renders a single trip row (7 lines) matching hardware layout exactly."""
        display_width = self.config.panel_width * self.config.num_panels
        
        # 1. Prepare segments
        route_text = str(dep['route'])
        headsign_text = dep['headsign']
        time_text = "Now" if dep['diff'] <= 0 else f"{dep['diff']}m"
        is_realtime = dep['live']
        
        # 2. Get bitmaps
        route_bm = self.microfont.get_bitmap(route_text)
        route_w = len(route_bm[0])
        
        time_bm = self.microfont.get_bitmap(time_text)
        time_w = len(time_bm[0])
        
        # 3. Calculate Headsign Scroll
        # Headsign area is between route and time
        headsign_x_start = route_w + 3
        icon_w = 6 if is_realtime else 0
        headsign_area_w = display_width - headsign_x_start - time_w - (icon_w + 2 if is_realtime else 0)
        
        headsign_bm_full = self.microfont.get_bitmap(headsign_text)
        headsign_full_w = len(headsign_bm_full[0])
        
        scroll_offset = 0
        if self.config.scroll_headsigns and headsign_full_w > headsign_area_w:
            overflow = headsign_full_w - headsign_area_w
            # Standard hardware-like scroll timing
            scroll_speed = 0.1 # Seconds per pixel
            scroll_duration = overflow * scroll_speed
            wait_duration = 2.0
            total_cycle = (wait_duration + scroll_duration) * 2
            
            cycle_pos = elapsed % total_cycle
            if cycle_pos < wait_duration:
                scroll_offset = 0
            elif cycle_pos < (wait_duration + scroll_duration):
                progress = (cycle_pos - wait_duration) / scroll_duration
                scroll_offset = int(progress * overflow)
            elif cycle_pos < (wait_duration * 2 + scroll_duration):
                scroll_offset = overflow
            else:
                progress = (cycle_pos - (wait_duration * 2 + scroll_duration)) / scroll_duration
                scroll_offset = overflow - int(progress * overflow)

        # 4. Construct Row Pixels (Full Width Canvas)
        canvas = [[None for _ in range(display_width)] for _ in range(7)]
        
        # Draw Route (at x=0)
        for r in range(7):
            for c in range(route_w):
                if c < display_width and route_bm[r][c]: canvas[r][c] = dep['color']
                
        # Draw Time (right-aligned)
        time_x = display_width - time_w
        time_color = "bright_blue" if is_realtime else "grey74"
        for r in range(7):
            for c in range(time_w):
                tx = time_x + c
                if 0 <= tx < display_width and time_bm[r][c]: canvas[r][tx] = time_color
                
        # Draw Icon (left of time)
        if is_realtime:
            icon_bm = self.microfont.get_live_icon_frame(elapsed)
            icon_x = time_x - 8 # 2px gap + 6px icon
            icon_color = "white"
            icon_color_dark = "bright_blue"
            for r in range(6):
                for c in range(6):
                    ix = icon_x + c
                    if 0 <= ix < display_width:
                        val = icon_bm[r][c]
                        if val == 2:
                            canvas[r][ix] = icon_color
                        elif val == 1:
                            canvas[r][ix] = icon_color_dark

        # Draw Headsign (with clipping and scroll)
        for r in range(7):
            for c in range(headsign_area_w):
                src_c = c + scroll_offset
                dest_x = headsign_x_start + c
                if 0 <= dest_x < display_width and src_c < headsign_full_w and headsign_bm_full[r][src_c]:
                    canvas[r][dest_x] = "white"

        # 5. Convert Canvas to Rich Text lines
        rich_lines = []
        for r in range(7):
            line = Text(no_wrap=True)
            for c in range(display_width):
                color = canvas[r][c]
                if color:
                    line.append("●", style=f"bold {color}")
                else:
                    line.append("·", style="dim black")
            rich_lines.append(line)
        return rich_lines

    def normalize_id(self, item_id: str) -> str:
        """Strip internal feed prefix and handle WSF special cases."""
        if not item_id: return ""
        s_id = str(item_id)
        if s_id.startswith("wsf:"):
            return s_id.replace("wsf:", "95_")
            
        if ":" in s_id and "_" in s_id:
            c_idx = s_id.find(":")
            u_idx = s_id.find("_")
            if c_idx < u_idx:
                return s_id[c_idx+1:]
        return s_id

    def get_upcoming_departures(self, reference_time: Optional[datetime] = None) -> list[dict]:
        """Returns a list of sorted departures currently being tracked."""
        all_departures = []
        now = reference_time or datetime.now(timezone.utc)
        current_time_ms = int(now.timestamp() * 1000)
        now_ts = now.timestamp()

        # MOCK STATE HANDLING
        is_mock = "mock" in self.state
        if is_mock:
            mock_data = self.state["mock"]["trips"]
            for mock_bus in mock_data:
                all_departures.append({
                    "trip_id": mock_bus.get("trip_id", str(time.time())), 
                    "diff": mock_bus.get("diff", 0),
                    "route": mock_bus.get("route", "??"),
                    "headsign": mock_bus.get("headsign", "Mock Data"),
                    "color": mock_bus.get("color", "yellow"),
                    "live": mock_bus.get("live", False)
                })
        else:
            live_data = self.state.get("live")
            if live_data and (now_ts - live_data.get("timestamp", 0) <= 600): # Increased to 10m
                for trip in live_data.get("trips", []):
                    # Filter using normalized IDs
                    trip_route_id = self.normalize_id(trip.get("routeId", ""))
                    trip_stop_id = self.normalize_id(trip.get("stopId", ""))
                    
                    sub = None
                    for s in self.config.subscriptions:
                        sub_route_id = self.normalize_id(s.route)
                        sub_stop_id = self.normalize_id(s.stop)
                        
                        if (sub_route_id == trip_route_id) and (sub_stop_id == trip_stop_id):
                            sub = s
                            break
                    
                    if not sub: continue
                    trip_id = trip.get("tripId")
                    if not trip_id: continue
                    if any(d.get("trip_id") == trip_id for d in all_departures): continue

                    arr_val = trip.get("arrivalTime") or trip.get("predictedArrivalTime") or trip.get("scheduledArrivalTime")
                    dep_val = trip.get("departureTime") or trip.get("predictedDepartureTime") or trip.get("scheduledDepartureTime") or arr_val
                    
                    if arr_val is None: continue
                    
                    display_mode = getattr(self.config, "time_display", "arrival")
                    
                    now_minus_buffer_ms = current_time_ms - (3600 * 1000)
                    
                    if display_mode == "departure":
                        base_val = dep_val if (dep_val and dep_val > now_minus_buffer_ms / 1000) else arr_val
                    else:
                        base_val = arr_val if (arr_val and arr_val > now_minus_buffer_ms / 1000) else dep_val

                    if base_val is None: continue

                    if isinstance(base_val, str):
                        try:
                            dt = datetime.fromisoformat(base_val.replace("Z", "+00:00"))
                            base_time_ms = int(dt.timestamp() * 1000)
                        except ValueError: continue
                    elif base_val > 10**12: base_time_ms = base_val
                    else: base_time_ms = base_val * 1000

                    if base_time_ms < now_minus_buffer_ms:
                        continue

                    # Calculate Offset
                    # BOTH Local and Cloud proxies now apply offsets on the server-side
                    # as part of the TJ Horner protocol alignment. The simulator (like the HW)
                    # should now treat the arrivalTime as the 'final' display time.
                    
                    raw_diff_sec = (base_time_ms - current_time_ms) / 1000.0
                    display_mins = int(raw_diff_sec / 60)
                    
                    # Filter logic:
                    if display_mins >= -1:
                        route_name = str(trip.get("routeName") or trip.get("routeShortName") or "")
                        if not route_name:
                            route_name = sub.label.split("-")[0].strip().split()[0] if sub and sub.label else sub.route.split("_")[-1]
                        
                        headsign = trip.get("headsign")
                        
                        # Fallback for old data or direct simulator runs
                        vehicle_id_full = trip.get("vehicleId")
                        if vehicle_id_full and ("95_" in vehicle_id_full or "wsf" in route_name.lower()):
                            vehicle_id_short = vehicle_id_full.split("_")[-1]
                            vessel_name = WSF_VESSELS.get(vehicle_id_short)
                            if vessel_name:
                                route_name = vessel_name.upper()
                                
                        if not headsign:
                            if sub:
                                headsign = sub.label.split("-")[-1].strip() if "-" in sub.label else sub.label
                        
                        is_live = trip.get("isRealtime", False)
                        
                        color_hex = trip.get("routeColor")
                        if "14" in route_name: color = "hot_pink"
                        elif color_hex: color = f"#{color_hex}"
                        else: color = "yellow"

                        if display_mins < 0: display_mins = 0

                        all_departures.append({
                            "trip_id": trip_id, "diff": display_mins, 
                            "route": route_name, "headsign": headsign or "Transit",
                            "color": color, "live": is_live,
                            "stop_id": trip_stop_id # Keep track of stop for diversity capping
                        })

        all_departures.sort(key=lambda x: x["diff"])
        
        # Apply "Fair" Diversity Capping to match hardware/proxy behavior
        # We want to ensure at least one trip from each stop is shown if possible.
        limit = 3 # Hardware/Simulator standard limit
        final_departures = []
        seen_stops = set()
        
        # Pass 1: Get the soonest arrival for every stop
        for dep in all_departures:
            stop_id = dep.get("stop_id")
            if stop_id not in seen_stops:
                final_departures.append(dep)
                seen_stops.add(stop_id)
            if len(final_departures) >= limit:
                break
                
        # Pass 2: Fill remaining slots with the next soonest arrivals overall
        if len(final_departures) < limit:
            for dep in all_departures:
                if dep not in final_departures:
                    final_departures.append(dep)
                if len(final_departures) >= limit:
                    break
        
        final_departures.sort(key=lambda x: x["diff"])
        return final_departures

    def get_current_display_text(self) -> str:
        """Returns a string representation of the current display (e.g., '14 Downtown 2m')."""
        deps = self.get_upcoming_departures()
        lines = []
        for d in deps[:3]:
            route_str = str(d['route'])
            time_str = "Now" if d['diff'] <= 0 else f"{d['diff']}m"
            live_flag = "{LIVE}" if d['live'] else ""
            
            route_w = len(self.microfont.get_bitmap(route_str)[0])
            time_w = len(self.microfont.get_bitmap(time_str)[0])
            icon_w = 6 if d['live'] else 0
            
            display_width = self.config.panel_width * self.config.num_panels
            headsign_x_start = route_w + 3
            headsign_area_w = display_width - headsign_x_start - time_w - (icon_w + 2 if d['live'] else 0)
            
            headsign = d['headsign']
            while headsign and len(self.microfont.get_bitmap(headsign)[0]) > headsign_area_w:
                headsign = headsign[:-1]
                
            lines.append(f"{route_str} {headsign} {live_flag}{time_str}")
        return "\n".join(lines)

    def _generate_frame(self, reference_time: Optional[datetime] = None) -> Panel:
        all_departures = self.get_upcoming_departures(reference_time)
        
        real_elapsed = time.time() - self.start_time
        elapsed = 0 if reference_time else real_elapsed
        is_mock = "mock" in self.state
        
        all_lines = []
        if not all_departures:
            msg = "Connecting..." if not self.state else ("No Mock Buses" if is_mock else "No Live Buses")
            # Create a simple message rendering
            bm = self.microfont.get_bitmap(msg)
            for r in range(7):
                line = Text(no_wrap=True)
                for c in range(len(bm[0])):
                    line.append("●" if bm[r][c] else "·", style="bold cyan" if bm[r][c] else "dim black")
                all_lines.append(line)
        else:
            for dep in all_departures[:3]: 
                all_lines.extend(self._render_trip_row(dep, elapsed))
                all_lines.append(Text("")) # Spacer row

        panel_title = f"[bold red]HUB75 {64 * self.config.num_panels}x32 LED SIMULATOR[/bold red]"
        if is_mock:
            panel_title += " [yellow](MOCK DATA)[/yellow]"
        else:
            source = "LOCAL" if "localhost" in self.config.api_url or "127.0.0.1" in self.config.api_url else self.config.api_url.replace("wss://", "").replace("ws://", "").split('/')[0]
            panel_title += f" [green](LIVE from {source})[/green]"

        return Panel(
            Group(*all_lines),
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

async def async_run_simulator(config: TransitConfig, force_live: bool = False):
    if not config.subscriptions and not config.mock_state and not config.captures:
        Console().print("[bold red]Error:[/bold red] No stops or mock state/captures configured.")
        return
    sim = LEDSimulator(config, force_live=force_live)
    try:
        await sim.run()
    except KeyboardInterrupt:
        pass

def run_simulator(config: TransitConfig, force_live: bool = False):
    try:
        asyncio.run(async_run_simulator(config, force_live))
    except KeyboardInterrupt:
        pass
