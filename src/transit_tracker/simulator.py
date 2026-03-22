"""LED matrix simulator with base class for shared logic and renderer subclasses.

BaseSimulator handles config loading, WebSocket connection lifecycle, trip data
processing, subscription message construction, ID normalization, and ferry
vessel mapping.  Subclasses (TUISimulator, etc.) only implement rendering.
"""

import asyncio
import json
import os
import re
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

import websockets

from .config import TransitConfig
from .display import build_bitmap_segments
from .network.websocket_server import WSF_VESSELS

# ---------------------------------------------------------------------------
# MicroFont — shared font renderer for bitmap-based simulators
# ---------------------------------------------------------------------------


class MicroFont:
    """A minimal 5x7 proportional-style font implementation for exact LED simulation."""

    GLYPHS = {
        "0": [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        "1": [0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E],
        "2": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F],
        "3": [0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E],
        "4": [0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02],
        "5": [0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E],
        "6": [0x0E, 0x10, 0x10, 0x1E, 0x11, 0x11, 0x0E],
        "7": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08],
        "8": [0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E],
        "9": [0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C],
        "A": [0x04, 0x0A, 0x11, 0x11, 0x1F, 0x11, 0x11],
        "B": [0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E],
        "C": [0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E],
        "D": [0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C],
        "E": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F],
        "F": [0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10],
        "G": [0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F],
        "H": [0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11],
        "I": [0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E],
        "J": [0x07, 0x02, 0x02, 0x02, 0x02, 0x12, 0x0C],
        "K": [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
        "L": [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F],
        "M": [0x11, 0x1B, 0x15, 0x11, 0x11, 0x11, 0x11],
        "N": [0x11, 0x11, 0x19, 0x15, 0x13, 0x11, 0x11],
        "O": [0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        "P": [0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10],
        "Q": [0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D],
        "R": [0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11],
        "S": [0x0E, 0x11, 0x10, 0x0E, 0x01, 0x11, 0x0E],
        "T": [0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
        "U": [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E],
        "V": [0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04],
        "W": [0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11],
        "X": [0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11],
        "Y": [0x11, 0x11, 0x0A, 0x04, 0x04, 0x04, 0x04],
        "Z": [0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F],
        " ": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
        "m": [0x00, 0x00, 0x1A, 0x15, 0x15, 0x15, 0x15],
        ".": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x04],
        "-": [0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00],
        ">": [0x10, 0x08, 0x04, 0x08, 0x10, 0x00, 0x00],
        "(": [0x04, 0x08, 0x08, 0x08, 0x08, 0x08, 0x04],
        ")": [0x08, 0x04, 0x04, 0x04, 0x04, 0x04, 0x08],
        "/": [0x01, 0x02, 0x04, 0x08, 0x10, 0x00, 0x00],
        "?": [0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04],
    }

    REALTIME_ICON = [
        [0, 0, 0, 3, 3, 3],
        [0, 0, 3, 0, 0, 0],
        [0, 3, 0, 0, 2, 2],
        [3, 0, 0, 2, 0, 0],
        [3, 0, 2, 0, 0, 1],
        [3, 0, 2, 0, 1, 1],
    ]

    _bdf_font = None
    _bdf_loaded = False

    @classmethod
    def _get_bdf(cls):
        if not cls._bdf_loaded:
            cls._bdf_loaded = True
            font_path = os.path.join(
                os.path.dirname(__file__), "fonts", "tom-thumb.bdf"
            )
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
            glyph = cls.GLYPHS.get(char.upper(), cls.GLYPHS["?"])
            for i in range(7):
                bits = glyph[i]
                for b in range(4, -1, -1):
                    rows[i].append(1 if (bits & (1 << b)) else 0)
                rows[i].append(0)  # Gap
        return rows

    @classmethod
    def get_live_icon_frame(cls, elapsed: float) -> list[list[int]]:
        """Calculates the current frame of the 6x6 realtime icon.
        Returns 0 for transparent, 1 for dim segment, 2 for lit segment.
        """
        cycle_ms = int(elapsed * 1000) % 4000
        frame = 0
        if cycle_ms >= 3000:
            frame = (cycle_ms - 3000) // 200 + 1
            if frame > 5:
                frame = 5

        rows = [[] for _ in range(7)]
        for r in range(6):
            for c in range(6):
                seg = cls.REALTIME_ICON[r][c]
                if seg == 0:
                    rows[r].append(0)
                    continue

                is_lit = False
                if seg == 1 and frame in [1, 2, 3]:
                    is_lit = True
                elif seg == 2 and frame in [2, 3, 4]:
                    is_lit = True
                elif seg == 3 and frame in [3, 4, 5]:
                    is_lit = True

                rows[r].append(2 if is_lit else 1)
        rows[6] = [0] * 6
        return rows


# ---------------------------------------------------------------------------
# BaseSimulator — shared config, WS connection, and trip processing
# ---------------------------------------------------------------------------


class BaseSimulator(ABC):
    """Abstract base for LED matrix simulators.

    Handles:
    - Configuration and subscription management
    - WebSocket connection lifecycle (connect, subscribe, reconnect)
    - Trip data processing (filtering, sorting, diversity capping)
    - ID normalization and ferry vessel mapping
    """

    def __init__(self, config: TransitConfig, force_live: bool = True):
        self.config = config
        self.force_live = force_live
        self.state: dict = {}
        self.running = True
        self.start_time = time.time()
        self.microfont = MicroFont()

        if not self.force_live and (self.config.mock_state or self.config.captures):
            mock_data = self.config.mock_state
            if not mock_data and self.config.captures:
                mock_data = self._parse_capture(self.config.captures[-1])
            self.state = {"mock": {"trips": mock_data or [], "timestamp": time.time()}}

    # -- Config & subscription helpers --

    @staticmethod
    def _parse_capture(capture: dict) -> list[dict]:
        """Parse a display capture dict into mock trip data."""
        display_text = capture.get("display", "").strip()
        mock_data = []
        for line in display_text.split("\n"):
            parts = line.split()
            if not parts:
                continue
            route = parts[0]
            live = "{LIVE}" in line
            time_str = parts[-1].replace("{LIVE}", "").replace("m", "")
            try:
                diff = int(time_str)
            except ValueError:
                diff = 0
            headsign = " ".join(line.replace("{LIVE}", " ").split()[1:-1])
            mock_data.append(
                {
                    "route": route,
                    "headsign": headsign,
                    "diff": diff,
                    "live": live,
                    "color": "hot_pink" if route == "14" else "yellow",
                }
            )
        return mock_data

    def build_subscribe_payload(
        self, client_name: str = "Simulator", limit: int = 10
    ) -> dict:
        """Build the schedule:subscribe WebSocket message."""
        pairs = []
        for sub in self.config.subscriptions:
            r_id = sub.route if ":" in sub.route else f"{sub.feed}:{sub.route}"
            s_id = sub.stop if ":" in sub.stop else f"{sub.feed}:{sub.stop}"

            off_sec = 0
            try:
                match = re.search(r"(-?\d+)", str(sub.time_offset))
                if match:
                    off_sec = int(match.group(1)) * 60
            except Exception:
                pass

            pairs.append(f"{r_id},{s_id},{off_sec}")

        return {
            "event": "schedule:subscribe",
            "client_name": client_name,
            "data": {
                "routeStopPairs": ";".join(pairs),
                "limit": limit,
            },
        }

    def resolve_ws_url(self) -> str:
        """Resolve the WebSocket URL, preferring local container when use_local_api."""
        api_url = self.config.api_url
        if self.config.service.use_local_api:
            if "localhost" not in api_url and "127.0.0.1" not in api_url:
                api_url = "ws://localhost:8000"
        return api_url.rstrip("/")

    # -- WebSocket connection lifecycle --

    async def _listen_websocket(self):
        """Connect to WS server, subscribe, and listen for updates."""
        if not self.force_live and "mock" in self.state:
            return

        api_url = self.resolve_ws_url()

        while self.running:
            try:
                async with websockets.connect(api_url) as ws:
                    payload = self.build_subscribe_payload()
                    await ws.send(json.dumps(payload))

                    async for message in ws:
                        if not self.running:
                            break
                        data = json.loads(message)
                        if data.get("event") == "schedule":
                            d = data.get("data", {})
                            self.state["live"] = {
                                "trips": d.get("trips", []),
                                "timestamp": time.time(),
                            }
                            self.on_trips_updated(d.get("trips", []))
            except Exception:
                if self.running:
                    await asyncio.sleep(5)

    def on_trips_updated(self, trips: list[dict]):  # noqa: B027
        """Hook called when new trip data arrives. Override in subclasses."""

    # -- ID normalization --

    @staticmethod
    def normalize_id(item_id: str) -> str:
        """Strip internal feed prefix and handle WSF special cases."""
        if not item_id:
            return ""
        s_id = str(item_id)
        if s_id.startswith("wsf:"):
            return s_id.replace("wsf:", "95_")
        if ":" in s_id and "_" in s_id:
            c_idx = s_id.find(":")
            u_idx = s_id.find("_")
            if c_idx < u_idx:
                return s_id[c_idx + 1 :]
        return s_id

    # -- Trip processing --

    def get_upcoming_departures(
        self, reference_time: Optional[datetime] = None
    ) -> list[dict]:
        """Returns sorted, diversity-capped departures currently being tracked."""
        all_departures = []
        now = reference_time or datetime.now(timezone.utc)
        current_time_ms = int(now.timestamp() * 1000)
        now_ts = now.timestamp()

        is_mock = "mock" in self.state
        if is_mock:
            mock_data = self.state["mock"]["trips"]
            for mock_bus in mock_data:
                all_departures.append(
                    {
                        "trip_id": mock_bus.get("trip_id", str(time.time())),
                        "diff": mock_bus.get("diff", 0),
                        "route": mock_bus.get("route", "??"),
                        "headsign": mock_bus.get("headsign", "Mock Data"),
                        "color": mock_bus.get("color", "yellow"),
                        "live": mock_bus.get("live", False),
                    }
                )
        else:
            live_data = self.state.get("live")
            if live_data and (now_ts - live_data.get("timestamp", 0) <= 600):
                for trip in live_data.get("trips", []):
                    dep = self._process_trip(trip, current_time_ms)
                    if dep:
                        if not any(
                            d.get("trip_id") == dep["trip_id"] for d in all_departures
                        ):
                            all_departures.append(dep)

        all_departures.sort(key=lambda x: x["diff"])
        return self._apply_diversity_cap(all_departures, limit=3)

    def _process_trip(self, trip: dict, current_time_ms: int) -> Optional[dict]:
        """Process a single raw trip from the server into a display departure dict."""
        trip_route_id = self.normalize_id(trip.get("routeId", ""))
        trip_stop_id = self.normalize_id(trip.get("stopId", ""))

        sub = None
        for s in self.config.subscriptions:
            if (
                self.normalize_id(s.route) == trip_route_id
                and self.normalize_id(s.stop) == trip_stop_id
            ):
                sub = s
                break

        if not sub:
            return None
        trip_id = trip.get("tripId")
        if not trip_id:
            return None

        arr_val = (
            trip.get("arrivalTime")
            or trip.get("predictedArrivalTime")
            or trip.get("scheduledArrivalTime")
        )
        dep_val = (
            trip.get("departureTime")
            or trip.get("predictedDepartureTime")
            or trip.get("scheduledDepartureTime")
            or arr_val
        )

        if arr_val is None:
            return None

        display_mode = self.config.transit_tracker.time_display
        now_minus_buffer_ms = current_time_ms - (3600 * 1000)

        if display_mode == "departure":
            base_val = (
                dep_val
                if (dep_val and dep_val > now_minus_buffer_ms / 1000)
                else arr_val
            )
        else:
            base_val = (
                arr_val
                if (arr_val and arr_val > now_minus_buffer_ms / 1000)
                else dep_val
            )

        if base_val is None:
            return None

        if isinstance(base_val, str):
            try:
                dt = datetime.fromisoformat(base_val.replace("Z", "+00:00"))
                base_time_ms = int(dt.timestamp() * 1000)
            except ValueError:
                return None
        elif base_val > 10**12:
            base_time_ms = base_val
        else:
            base_time_ms = base_val * 1000

        if base_time_ms < now_minus_buffer_ms:
            return None

        raw_diff_sec = (base_time_ms - current_time_ms) / 1000.0
        display_mins = int(raw_diff_sec / 60)

        if display_mins < -1:
            return None

        route_name = str(trip.get("routeName") or trip.get("routeShortName") or "")
        if not route_name:
            route_name = (
                sub.label.split("-")[0].strip().split()[0]
                if sub and sub.label
                else sub.route.split("_")[-1]
            )

        headsign = trip.get("headsign")

        vehicle_id_full = trip.get("vehicleId")
        if vehicle_id_full and (
            "95_" in vehicle_id_full or "wsf" in route_name.lower()
        ):
            vehicle_id_short = vehicle_id_full.split("_")[-1]
            vessel_name = WSF_VESSELS.get(vehicle_id_short)
            if vessel_name:
                headsign = vessel_name

        if not headsign:
            if sub:
                headsign = (
                    sub.label.split("-")[-1].strip() if "-" in sub.label else sub.label
                )

        is_live = trip.get("isRealtime", False)

        color_hex = trip.get("routeColor")
        if "14" in route_name:
            color = "hot_pink"
        elif color_hex:
            color = f"#{color_hex}"
        else:
            color = "yellow"

        if display_mins < 0:
            display_mins = 0

        return {
            "trip_id": trip_id,
            "diff": display_mins,
            "route": route_name,
            "headsign": headsign or "Transit",
            "color": color,
            "live": is_live,
            "stop_id": trip_stop_id,
        }

    @staticmethod
    def _apply_diversity_cap(departures: list[dict], limit: int = 3) -> list[dict]:
        """Fair diversity capping: at least one trip per stop, then fill by soonest."""
        final = []
        seen_stops: set = set()

        for dep in departures:
            stop_id = dep.get("stop_id")
            if stop_id not in seen_stops:
                final.append(dep)
                seen_stops.add(stop_id)
            if len(final) >= limit:
                break

        if len(final) < limit:
            for dep in departures:
                if dep not in final:
                    final.append(dep)
                if len(final) >= limit:
                    break

        final.sort(key=lambda x: x["diff"])
        return final

    def get_current_display_text(self) -> str:
        """Returns a plain-text representation of the current display."""
        deps = self.get_upcoming_departures()
        fmt = self.config.transit_tracker.display_format
        display_width = self.config.service.panel_width * self.config.service.num_panels
        lines = []
        for d in deps[:3]:
            segments = build_bitmap_segments(d, fmt=fmt)

            fixed_width = 0
            for seg in segments:
                if seg["variable"] == "HEADSIGN":
                    continue
                if seg["variable"] == "LIVE" and d.get("live"):
                    fixed_width += 8
                elif seg["text"]:
                    bm = self.microfont.get_bitmap(seg["text"])
                    fixed_width += len(bm[0])
            headsign_area_w = max(0, display_width - fixed_width)

            parts = []
            for seg in segments:
                if seg["variable"] == "LIVE":
                    if d.get("live"):
                        parts.append("{LIVE}")
                    continue
                if seg["variable"] == "HEADSIGN":
                    headsign = seg["text"]
                    while (
                        headsign
                        and len(self.microfont.get_bitmap(headsign)[0])
                        > headsign_area_w
                    ):
                        headsign = headsign[:-1]
                    parts.append(headsign)
                    continue
                if seg["text"]:
                    parts.append(seg["text"])
            lines.append("".join(parts))
        return "\n".join(lines)

    # -- Abstract interface for subclasses --

    @abstractmethod
    async def run(self):
        """Start the simulator display loop."""


# ---------------------------------------------------------------------------
# TUISimulator — Rich-based terminal LED matrix emulator
# ---------------------------------------------------------------------------


class TUISimulator(BaseSimulator):
    """Terminal LED matrix simulator using Rich for rendering."""

    def _render_trip_row(self, dep: dict, elapsed: float) -> list:
        """Renders a single trip row (7 lines) matching hardware layout exactly."""
        from rich.text import Text

        display_width = self.config.service.panel_width * self.config.service.num_panels
        fmt = self.config.transit_tracker.display_format
        segments = build_bitmap_segments(dep, fmt=fmt)

        fixed_width = 0
        for seg in segments:
            if seg["variable"] == "HEADSIGN":
                continue
            if seg["variable"] == "LIVE" and dep.get("live"):
                fixed_width += 8
            elif seg["text"]:
                bm = self.microfont.get_bitmap(seg["text"])
                fixed_width += len(bm[0])

        headsign_area_w = max(0, display_width - fixed_width)

        rendered: list[dict] = []
        for seg in segments:
            var = seg["variable"]
            if var == "LIVE":
                if dep.get("live"):
                    rendered.append({"type": "icon", "role": seg["role"], "width": 8})
                continue
            if var == "HEADSIGN":
                text = seg["text"]
                bm_full = self.microfont.get_bitmap(text)
                full_w = len(bm_full[0])
                scroll_offset = 0
                if (
                    self.config.transit_tracker.scroll_headsigns
                    and full_w > headsign_area_w > 0
                ):
                    overflow = full_w - headsign_area_w
                    scroll_speed = 0.1
                    scroll_dur = overflow * scroll_speed
                    wait = 2.0
                    total_cycle = (wait + scroll_dur) * 2
                    cp = elapsed % total_cycle
                    if cp < wait:
                        scroll_offset = 0
                    elif cp < wait + scroll_dur:
                        p = (cp - wait) / scroll_dur
                        scroll_offset = int(p * overflow)
                    elif cp < wait * 2 + scroll_dur:
                        scroll_offset = overflow
                    else:
                        p = (cp - wait * 2 - scroll_dur) / scroll_dur
                        scroll_offset = overflow - int(p * overflow)
                rendered.append(
                    {
                        "type": "headsign",
                        "bitmap": bm_full,
                        "full_w": full_w,
                        "area_w": headsign_area_w,
                        "scroll_offset": scroll_offset,
                        "color": seg["role"],
                    }
                )
                continue
            if not seg["text"]:
                continue
            bm = self.microfont.get_bitmap(seg["text"])
            rendered.append(
                {
                    "type": "text",
                    "bitmap": bm,
                    "width": len(bm[0]),
                    "color": seg["role"],
                }
            )

        canvas = [[None] * display_width for _ in range(7)]
        x = 0
        for item in rendered:
            if item["type"] == "text":
                bm = item["bitmap"]
                w = item["width"]
                for r in range(7):
                    for c in range(w):
                        px = x + c
                        if 0 <= px < display_width and bm[r][c]:
                            canvas[r][px] = item["color"]
                x += w
            elif item["type"] == "headsign":
                bm = item["bitmap"]
                area_w = item["area_w"]
                so = item["scroll_offset"]
                for r in range(7):
                    for c in range(area_w):
                        src = c + so
                        px = x + c
                        if (
                            0 <= px < display_width
                            and src < item["full_w"]
                            and bm[r][src]
                        ):
                            canvas[r][px] = item["color"]
                x += area_w
            elif item["type"] == "icon":
                icon_bm = self.microfont.get_live_icon_frame(elapsed)
                ix = x + 1
                for r in range(6):
                    for c in range(6):
                        px = ix + c
                        if 0 <= px < display_width:
                            val = icon_bm[r][c]
                            if val == 2:
                                canvas[r][px] = "white"
                            elif val == 1:
                                canvas[r][px] = "bright_blue"
                x += 8

        rich_lines = []
        for r in range(7):
            line = Text(no_wrap=True)
            for c in range(display_width):
                color = canvas[r][c]
                if color:
                    line.append("\u25cf", style=f"bold {color}")
                else:
                    line.append("\u00b7", style="dim black")
            rich_lines.append(line)
        return rich_lines

    def _generate_frame(self, reference_time: Optional[datetime] = None):
        from rich.console import Group
        from rich.panel import Panel
        from rich.text import Text

        all_departures = self.get_upcoming_departures(reference_time)

        real_elapsed = time.time() - self.start_time
        elapsed = 0 if reference_time else real_elapsed
        is_mock = "mock" in self.state

        all_lines = []
        if not all_departures:
            msg = (
                "Connecting..."
                if not self.state
                else ("No Mock Buses" if is_mock else "No Live Buses")
            )
            bm = self.microfont.get_bitmap(msg)
            for r in range(7):
                line = Text(no_wrap=True)
                for c in range(len(bm[0])):
                    line.append(
                        "\u25cf" if bm[r][c] else "\u00b7",
                        style="bold cyan" if bm[r][c] else "dim black",
                    )
                all_lines.append(line)
        else:
            for dep in all_departures[:3]:
                all_lines.extend(self._render_trip_row(dep, elapsed))
                all_lines.append(Text(""))

        panel_title = (
            f"[bold red]HUB75 {64 * self.config.service.num_panels}x32"
            f" LED SIMULATOR[/bold red]"
        )
        if is_mock:
            panel_title += " [yellow](MOCK DATA)[/yellow]"
        else:
            api_url = self.config.api_url
            source = (
                "LOCAL"
                if "localhost" in api_url or "127.0.0.1" in api_url
                else api_url.replace("wss://", "").replace("ws://", "").split("/")[0]
            )
            panel_title += f" [green](LIVE from {source})[/green]"

        return Panel(
            Group(*all_lines),
            title=panel_title,
            subtitle="[dim]Ctrl+C to Exit[/dim]",
            border_style="red",
            style="on black",
            padding=(0, 1),
            expand=False,
        )

    async def run(self):
        from rich.live import Live

        ws_task = asyncio.create_task(self._listen_websocket())
        try:
            with Live(
                self._generate_frame(), refresh_per_second=10, screen=True
            ) as live:
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


# Backward-compatible alias
LEDSimulator = TUISimulator


# ---------------------------------------------------------------------------
# Module-level convenience functions (backward-compatible)
# ---------------------------------------------------------------------------


async def async_run_simulator(config: TransitConfig, force_live: bool = False):
    from rich.console import Console

    if not config.subscriptions and not config.mock_state and not config.captures:
        Console().print(
            "[bold red]Error:[/bold red] No stops or mock state/captures configured."
        )
        return
    sim = TUISimulator(config, force_live=force_live)
    try:
        await sim.run()
    except KeyboardInterrupt:
        pass


def run_simulator(config: TransitConfig, force_live: bool = False):
    try:
        asyncio.run(async_run_simulator(config, force_live))
    except KeyboardInterrupt:
        pass
