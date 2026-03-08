import asyncio
import httpx
import os
import sys
import time
from datetime import datetime, timezone
from bdfparser import Font
from rich.live import Live
from rich.text import Text
from rich.panel import Panel
from rich.console import Console, Group

from .config import TransitConfig

class LEDSimulator:
    def __init__(self, config: TransitConfig):
        # VERSION: 2026-03-08-UNIFIED-POLL
        self.config = config
        self.state = {} # stopId -> { routeId -> [arrivals], 'timestamp' -> float }
        self.route_colors = {} # routeId -> hex color
        self.running = True
        self.start_time = time.time()

        # Load a tiny bitmap font that mimics LED displays
        font_path = os.path.join(os.path.dirname(__file__), "fonts", "tom-thumb.bdf")
        if not os.path.exists(font_path):
            self.font = None
        else:
            self.font = Font(font_path)

    async def _poll_oba(self):
        # MOCK MODE: Priority 1: Explicit mock_state, Priority 2: Latest Capture
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

        if mock_data:
            self.state = {"mock": {"all": mock_data, "timestamp": time.time()}}
            return

        base_url = "https://api.pugetsound.onebusaway.org/api/where"
        oba_key = "TEST"

        # Group subscriptions by stop to minimize API calls
        stops_to_poll = {}
        for sub in self.config.subscriptions:
            if sub.stop not in stops_to_poll:
                stops_to_poll[sub.stop] = []
            stops_to_poll[sub.stop].append(sub)

        async with httpx.AsyncClient(timeout=10.0) as client:
            while self.running:
                for stop_id, subs in stops_to_poll.items():
                    if not self.running:
                        break
                    
                    stop_id_clean = stop_id.split(":")[-1] if ":" in stop_id else stop_id
                    url = f"{base_url}/arrivals-and-departures-for-stop/{stop_id_clean}.json"
                    
                    try:
                        response = await client.get(url, params={"key": oba_key})
                        if response.status_code == 200:
                            data = response.json()
                            
                            # Update colors
                            routes_ref = data.get("data", {}).get("references", {}).get("routes", [])
                            for r in routes_ref:
                                if "color" in r and r["color"]:
                                    self.route_colors[r["id"]] = f"#{r['color']}"

                            entries = data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
                            
                            # Process all subscriptions for this stop at once
                            new_stop_state = {"timestamp": time.time()}
                            for sub in subs:
                                target_route_id = sub.route.split(":")[-1] if ":" in sub.route else sub.route
                                target_short_name = target_route_id.split("_")[-1] if "_" in target_route_id else target_route_id
                                
                                filtered = []
                                for e in entries:
                                    rid = e.get("routeId", "")
                                    rsname = e.get("routeShortName", "")
                                    if (rid == target_route_id or rid.split(":")[-1] == target_route_id or 
                                        rsname == target_short_name or (target_short_name == "1" and rsname == "14")):
                                        filtered.append(e)
                                new_stop_state[sub.route] = filtered
                            
                            self.state[stop_id] = new_stop_state
                        
                        # Small delay between stops to avoid rate limiting
                        await asyncio.sleep(0.5)
                    except Exception:
                        pass
                
                # Poll every 30 seconds
                for _ in range(30):
                    if not self.running: break
                    await asyncio.sleep(1)

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
        current_time_ms = int(now.timestamp() * 1000)
        now_ts = now.timestamp()
        
        elapsed = 0 if reference_time else (time.time() - self.start_time)

        # MOCK STATE HANDLING
        if self.config.mock_state:
            # Check for "mock" key or just use the list if mock_state is already formatted
            mock_data = self.state.get("mock", {}).get("all", self.config.mock_state)
            for mock_bus in mock_data:
                all_departures.append({
                    "diff": mock_bus.get("diff", 0),
                    "route": mock_bus.get("route", "??"),
                    "headsign": mock_bus.get("headsign", "Mock Data"),
                    "color": mock_bus.get("color", "yellow"),
                    "live": mock_bus.get("live", False)
                })
        else:
            for stop_id, stop_data in self.state.items():
                # Stale data check: ignore data older than 5 minutes
                if now_ts - stop_data.get("timestamp", 0) > 300:
                    continue

                for route_id, trips in stop_data.items():
                    if route_id == "timestamp": continue
                    
                    sub = next((s for s in self.config.subscriptions if s.stop == stop_id and s.route == route_id), None)
                    if not sub: continue

                    offset_sec = 0
                    if sub.time_offset:
                        try:
                            clean_offset = sub.time_offset.lower().replace("min", "").strip()
                            offset_sec = int(clean_offset) * 60 if "min" in sub.time_offset.lower() else int(clean_offset)
                        except ValueError:
                            pass

                    for trip in trips:
                        arr_time_ms = trip.get("predictedArrivalTime") or trip.get("scheduledArrivalTime")
                        if not arr_time_ms: continue

                        raw_diff_sec = (arr_time_ms - current_time_ms) / 1000.0
                        eff_diff_sec = raw_diff_sec + offset_sec
                        
                        raw_mins = int(raw_diff_sec / 60)
                        eff_mins = int(eff_diff_sec / 60)

                        if raw_mins >= -2:
                            route_id_api = trip.get("routeId")
                            route_name = trip.get("routeShortName", sub.route.split("_")[-1] if "_" in sub.route else sub.route)
                            headsign = trip.get("tripHeadsign", sub.label.split("-")[-1].strip() if "-" in sub.label else sub.label)
                            is_live = trip.get("predicted", False)
                            color = self.route_colors.get(route_id_api, "yellow")

                            display_mins = raw_mins if self.config.time_display == "arrival" else eff_mins

                            all_departures.append({
                                "diff": display_mins, 
                                "route": route_name, 
                                "headsign": headsign,
                                "color": color,
                                "live": is_live
                            })

        all_departures.sort(key=lambda x: x["diff"])
        
        lines = []
        if not self.state and not self.config.mock_state:
            lines.append(self._render_led_string("Connecting...", color="cyan"))
        elif not all_departures:
            msg = "No Mock Buses" if self.config.mock_state else "No Live Buses"
            lines.append(self._render_led_string(msg, color="white"))
        else:
            char_width = 16 * self.config.num_panels
            for dep in all_departures[:4]: 
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
        if self.config.mock_state:
            panel_title += " [yellow](MOCK DATA)[/yellow]"

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
        poll_task = asyncio.create_task(self._poll_oba())
        try:
            with Live(self._generate_frame(), refresh_per_second=4, screen=True) as live:
                while True:
                    await asyncio.sleep(0.25)
                    live.update(self._generate_frame())
        except KeyboardInterrupt:
            pass
        finally:
            self.running = False
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

def run_simulator(config: TransitConfig):
    if not config.subscriptions and not config.mock_state:
        Console().print("[bold red]Error:[/bold red] No stops or mock state configured.")
        return
    sim = LEDSimulator(config)
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        pass
