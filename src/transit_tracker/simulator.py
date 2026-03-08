import asyncio
import httpx
import os
import sys
from datetime import datetime, timezone
from bdfparser import Font
from rich.live import Live
from rich.text import Text
from rich.panel import Panel
from rich.console import Console, Group

from .config import TransitConfig

class LEDSimulator:
    def __init__(self, config: TransitConfig):
        self.config = config
        self.state = {} # stopId -> list of departures
        self.route_colors = {} # routeId -> hex color
        self.running = True
        
        # Load a tiny bitmap font that mimics LED displays
        font_path = os.path.join(os.path.dirname(__file__), "fonts", "tom-thumb.bdf")
        if not os.path.exists(font_path):
            self.font = None
        else:
            self.font = Font(font_path)

    async def _poll_oba(self):
        base_url = "https://api.pugetsound.onebusaway.org/api/where"
        oba_key = "TEST"

        async with httpx.AsyncClient(timeout=10.0) as client:
            while self.running:
                for sub in self.config.subscriptions:
                    if not self.running:
                        break
                    
                    stop_id = sub.stop.split(":")[-1] if ":" in sub.stop else sub.stop
                    route_id = sub.route.split(":")[-1] if ":" in sub.route else sub.route

                    url = f"{base_url}/arrivals-and-departures-for-stop/{stop_id}.json"
                    try:
                        response = await client.get(url, params={"key": oba_key})
                        if response.status_code == 200:
                            data = response.json()
                            
                            # Extract route colors from references
                            routes_ref = data.get("data", {}).get("references", {}).get("routes", [])
                            for r in routes_ref:
                                if "color" in r and r["color"]:
                                    self.route_colors[r["id"]] = f"#{r['color']}"

                            entries = data.get("data", {}).get("entry", {}).get("arrivalsAndDepartures", [])
                            filtered = [e for e in entries if e.get("routeId") == route_id]
                            self.state[sub.stop] = filtered
                    except Exception:
                        pass
                
                for _ in range(10):
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

    def _generate_frame(self) -> Panel:
        all_departures = []
        now = datetime.now(timezone.utc)
        current_time_ms = int(now.timestamp() * 1000)

        for sub in self.config.subscriptions:
            offset_sec = 0
            if sub.time_offset:
                try:
                    # Support formats like "-7min" or "-420"
                    clean_offset = sub.time_offset.lower().replace("min", "").strip()
                    offset_sec = int(clean_offset) * 60 if "min" in sub.time_offset.lower() else int(clean_offset)
                except ValueError:
                    pass

            trips = self.state.get(sub.stop, [])
            for trip in trips:
                arr_time_ms = trip.get("predictedArrivalTime") or trip.get("scheduledArrivalTime")
                if not arr_time_ms: continue

                # Raw time from API
                raw_diff_sec = (arr_time_ms - current_time_ms) / 1000.0
                
                # Apply offset
                eff_diff_sec = raw_diff_sec + offset_sec
                
                raw_mins = int(raw_diff_sec / 60)
                eff_mins = int(eff_diff_sec / 60)

                # Filter: Hide buses that have already departed (even after offset)
                if eff_mins >= -2:
                    route_id = trip.get("routeId")
                    route_name = trip.get("routeShortName", sub.route.split("_")[-1] if "_" in sub.route else sub.route)
                    headsign = trip.get("tripHeadsign", sub.label.split("-")[-1].strip() if "-" in sub.label else sub.label)
                    is_live = trip.get("predicted", False)
                    
                    # Use route color from API or default to yellow
                    color = self.route_colors.get(route_id, "yellow")
                    
                    # Respect time_display setting: "arrival" vs "departure"
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
        if not self.state:
            lines.append(self._render_led_string("Connecting...", color="cyan"))
        elif not all_departures:
            lines.append(self._render_led_string("No Upcoming Buses", color="white"))
        else:
            # Layout matching display: "14 Downtown Seattle 7m *"
            char_width = 16 * self.config.num_panels
            
            for dep in all_departures[:4]: 
                # Icon: '*' if live (dot-matrix icon)
                icon = "*" if dep["live"] else " "
                eta = "Due" if dep["diff"] <= 0 else f"{dep['diff']}m"
                
                # Math for padding:
                # Space(1) between parts = 10 chars used.
                max_h = char_width - 10 
                h_text = dep['headsign'][:max_h]
                
                line_str = f"{dep['route']:>2} {h_text:<{max_h}} {eta:>3} {icon}"
                lines.append(self._render_led_string(line_str, color=dep["color"]))

        panel_title = f"[bold red]HUB75 {64 * self.config.num_panels}x32 LED SIMULATOR[/bold red]"
        return Panel(
            Group(*lines),
            title=panel_title,
            subtitle=f"[dim]Mode: {self.config.time_display} | Ctrl+C to Exit[/dim]",
            border_style="red",
            style="on black",
            padding=(0, 1),
            expand=False
        )

    async def run(self):
        poll_task = asyncio.create_task(self._poll_oba())
        try:
            with Live(self._generate_frame(), refresh_per_second=1, screen=True) as live:
                while True:
                    await asyncio.sleep(1)
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
    if not config.subscriptions:
        Console().print("[bold red]Error:[/bold red] No stops configured.")
        return
    sim = LEDSimulator(config)
    try:
        asyncio.run(sim.run())
    except KeyboardInterrupt:
        pass
