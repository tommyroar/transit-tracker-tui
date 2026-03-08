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

    def _render_led_string(self, text: str, color: str = "yellow") -> Text:
        """Renders text as a dot-matrix style LED string using the bitmap font."""
        if not self.font:
            return Text(text, style=color, no_wrap=True)
            
        canvas = self.font.draw(text.upper(), mode=1)
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

                # Apply offset
                diff_sec = (arr_time_ms - current_time_ms) / 1000.0 + offset_sec
                diff_mins = int(diff_sec / 60)

                if diff_mins >= -2:
                    route_id = trip.get("routeId")
                    route_name = trip.get("routeShortName", sub.route.split("_")[-1] if "_" in sub.route else sub.route)
                    headsign = trip.get("tripHeadsign", sub.label.split("-")[-1].strip() if "-" in sub.label else sub.label)
                    
                    # Determine Color
                    if 0 <= diff_mins <= self.config.arrival_threshold_minutes:
                        color = "red"
                    else:
                        color = self.route_colors.get(route_id, "yellow")
                    
                    all_departures.append({
                        "diff": diff_mins, 
                        "route": route_name, 
                        "headsign": headsign,
                        "color": color
                    })

        all_departures.sort(key=lambda x: x["diff"])
        
        lines = []
        if not self.state:
            lines.append(self._render_led_string("CONNECTING TO API...", color="cyan"))
        elif not all_departures:
            lines.append(self._render_led_string("NO UPCOMING BUSES", color="white"))
        else:
            max_headsign = 15 if self.config.num_panels == 1 else 30
            for dep in all_departures[:4]: 
                eta = "DUE" if dep["diff"] <= 0 else f"{dep['diff']} MIN"
                
                if self.config.num_panels == 1:
                    line_str = f"{dep['route']:>3} {dep['headsign'][:15]:<15} {eta:>6}"
                else:
                    line_str = f"{dep['route']:>3} {dep['headsign'][:30]:<30} {eta:>6}"
                
                lines.append(self._render_led_string(line_str, color=dep["color"]))

        panel_title = f"[bold red]HUB75 {64 * self.config.num_panels}x32 LED SIMULATOR[/bold red]"
        return Panel(
            Group(*lines),
            title=panel_title,
            subtitle=f"[dim]Threshold: {self.config.arrival_threshold_minutes}m | Ctrl+C to Exit[/dim]",
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
