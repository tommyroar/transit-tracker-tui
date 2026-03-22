"""Live visual monitoring mode for Transit Tracker.

Renders a real-time network topology diagram showing the proxy server,
remote OBA provider, and connected/disconnected displays with actual
message representations flowing between them.

Usage::

    transit-tracker monitor          # default — poll service state
    transit-tracker monitor --ws     # subscribe to live WS data
"""

import asyncio
import json
import os
import time
from collections import deque
from typing import Any, Dict, List

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .config import TransitConfig, build_route_stop_pairs, get_last_config_path
from .logging import get_logger
from .network.websocket_server import get_service_state

log = get_logger("transit_tracker.monitor")

# ── constants ───────────────────────────────────────────────────────

_MAX_EVENTS = 40  # scrollback for the message log


# ── data model ──────────────────────────────────────────────────────

class MonitorState:
    """Accumulates state for the monitor display."""

    def __init__(self):
        self.service_state: Dict[str, Any] = {}
        self.trips: List[dict] = []
        self.events: deque = deque(maxlen=_MAX_EVENTS)
        self.last_heartbeat: float = 0
        self.last_schedule_ts: float = 0
        self.ws_connected: bool = False
        self.oba_last_call: float = 0
        self.oba_healthy: bool = True

    def ingest_state(self, state: Dict[str, Any]):
        """Update from service_state.json snapshot."""
        prev = self.service_state
        self.service_state = state

        hb = state.get("heartbeat", 0)
        if hb and hb != self.last_heartbeat:
            self.last_heartbeat = hb

        # Detect new schedule push
        lu = state.get("last_update", 0)
        if lu and lu != self.last_schedule_ts:
            self.last_schedule_ts = lu
            # Extract trips from last_message
            lm = state.get("last_message") or {}
            trips = lm.get("data", {}).get("trips", [])
            if trips:
                self.trips = trips
                self._add_event(
                    "schedule",
                    "server → clients",
                    f"{len(trips)} trips pushed",
                )

        # Detect client changes
        old_count = prev.get("client_count", 0)
        new_count = state.get("client_count", 0)
        if new_count > old_count:
            diff = new_count - old_count
            self._add_event(
                "connect",
                "client → server",
                f"+{diff} client(s) connected (total {new_count})",
            )
        elif new_count < old_count:
            diff = old_count - new_count
            self._add_event(
                "disconnect",
                "server ✕ client",
                f"-{diff} client(s) disconnected (total {new_count})",
            )

        # Rate-limit changes
        was_rl = prev.get("is_rate_limited", False)
        is_rl = state.get("is_rate_limited", False)
        if is_rl and not was_rl:
            self._add_event(
                "throttle",
                "OBA → server",
                "429 rate limited — backing off",
            )
        elif was_rl and not is_rl:
            self._add_event(
                "recovery",
                "OBA → server",
                "Rate limit cleared — recovering",
            )

        self.oba_healthy = not is_rl

        # Detect API calls
        old_calls = prev.get("api_calls_total", 0)
        new_calls = state.get("api_calls_total", 0)
        if new_calls > old_calls:
            diff = new_calls - old_calls
            self.oba_last_call = time.time()
            self._add_event(
                "api",
                "server → OBA",
                f"{diff} API call(s) (total {new_calls})",
            )

    def ingest_ws_message(self, raw: str):
        """Process a live WebSocket message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        event = msg.get("event", "?")
        if event == "schedule":
            trips = msg.get("data", {}).get("trips", [])
            self.trips = trips
            self._add_event(
                "schedule",
                "server → client",
                f"{len(trips)} trips",
            )
        elif event == "heartbeat":
            self.last_heartbeat = time.time()
            self._add_event("heartbeat", "server → client", "ping")

    def _add_event(self, kind: str, direction: str, detail: str):
        self.events.append({
            "ts": time.time(),
            "kind": kind,
            "direction": direction,
            "detail": detail,
        })


# ── rendering ───────────────────────────────────────────────────────

_KIND_STYLE = {
    "schedule": "bold green",
    "heartbeat": "dim",
    "connect": "bold cyan",
    "disconnect": "bold red",
    "throttle": "bold yellow",
    "recovery": "bold green",
    "api": "blue",
    "subscribe": "bold magenta",
}


def _ago(ts: float) -> str:
    if ts <= 0:
        return "never"
    d = time.time() - ts
    if d < 1:
        return "just now"
    if d < 60:
        return f"{int(d)}s ago"
    if d < 3600:
        return f"{int(d / 60)}m ago"
    return f"{d / 3600:.1f}h ago"


def _bar(value: float, max_val: float, width: int = 16) -> str:
    if max_val <= 0:
        return "░" * width
    filled = int(min(value / max_val, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


def render_topology(ms: MonitorState) -> Panel:
    """Build the network topology diagram."""
    state = ms.service_state
    clients = state.get("clients", [])
    client_count = state.get("client_count", 0)
    is_running = state.get("status") == "active"
    is_rl = state.get("is_rate_limited", False)
    refresh = state.get("refresh_interval", 30)
    api_calls = state.get("api_calls_total", 0)
    throttle = state.get("throttle_total", 0)
    msgs = state.get("messages_processed", 0)
    uptime_h = state.get("uptime_hours", 0)

    # ── OBA provider box ──
    oba_status = "[red]THROTTLED[/red]" if is_rl else "[green]HEALTHY[/green]"
    oba_call_ago = _ago(ms.oba_last_call)
    oba_lines = [
        f"  Status: {oba_status}",
        f"  Calls:  {api_calls}  Throttled: {throttle}",
        f"  Last:   {oba_call_ago}",
    ]
    oba_box = Text()
    oba_box.append("╭─── OneBusAway API ───╮\n", style="yellow")
    oba_box.append("│", style="yellow")
    oba_box.append(f"  Status: ", style="")
    if is_rl:
        oba_box.append("THROTTLED", style="bold red")
    else:
        oba_box.append("HEALTHY", style="bold green")
    oba_box.append("     │\n", style="yellow")
    oba_box.append("│", style="yellow")
    oba_box.append(f"  Calls: {api_calls:<6}", style="dim")
    oba_box.append(f" 429s: {throttle:<4}", style="dim")
    oba_box.append("│\n", style="yellow")
    oba_box.append("│", style="yellow")
    oba_box.append(f"  Last call: {oba_call_ago:<11}", style="dim")
    oba_box.append("│\n", style="yellow")
    oba_box.append("╰───────────────────────╯", style="yellow")

    # ── connection line OBA ↔ Server ──
    if is_rl:
        oba_wire = Text("        ╳╳╳ 429 ╳╳╳\n", style="red")
    elif ms.oba_last_call and time.time() - ms.oba_last_call < 5:
        oba_wire = Text("        ◄━━━━━━━━━━━ arrivals\n", style="green")
    else:
        oba_wire = Text("        │           │\n", style="dim")

    # ── Server box ──
    srv_icon = "●" if is_running else "○"
    srv_color = "green" if is_running else "red"
    server_box = Text()
    server_box.append("╭──── Transit Proxy ────╮\n", style="cyan")
    server_box.append("│", style="cyan")
    server_box.append(f"  {srv_icon} ", style=srv_color)
    if is_running:
        server_box.append("RUNNING", style=f"bold {srv_color}")
    else:
        server_box.append("STOPPED", style=f"bold {srv_color}")
    server_box.append(f"          │\n", style="cyan")
    server_box.append("│", style="cyan")
    server_box.append(f"  :8000 WebSocket      │\n", style="cyan")
    server_box.append("│", style="cyan")
    if uptime_h >= 1:
        up_str = f"{uptime_h:.1f}h"
    else:
        up_str = f"{int(uptime_h * 60)}m"
    server_box.append(f"  Up: {up_str:<5}", style="dim")
    server_box.append(f" Msgs: {msgs:<5}", style="dim")
    server_box.append("│\n", style="cyan")
    server_box.append("│", style="cyan")
    server_box.append(f"  Refresh: {refresh}s", style="dim")
    server_box.append(f"  Cache: {state.get('cache_size', '?')}", style="dim")
    # Pad to fill box width
    inner = f"  Refresh: {refresh}s  Cache: {state.get('cache_size', '?')}"
    pad = max(0, 22 - len(inner))
    server_box.append(" " * pad + "│\n", style="cyan")
    server_box.append("│", style="cyan")
    server_box.append(f"  Clients: {client_count}          ", style="dim")
    server_box.append("│\n", style="cyan")
    server_box.append("╰────────────────────────╯", style="cyan")

    # ── client boxes ──
    client_section = Text()
    if clients:
        for i, c in enumerate(clients):
            name = c.get("name", "Unknown")
            addr = c.get("address", "?:?").split(":")[0]
            subs = c.get("subscriptions", 0)
            is_local = addr in ("127.0.0.1", "localhost", "0.0.0.0")

            if i == 0:
                client_section.append(
                    "        ┣━━━━━━━━━━━▶ ",
                    style="green",
                )
            else:
                client_section.append(
                    "        ┣━━━━━━━━━━━▶ ",
                    style="green",
                )

            icon = "📺" if not is_local else "🖥 "
            client_section.append(f"{icon} ", style="")
            client_section.append(f"{name}", style="bold white")
            client_section.append(f" ({addr})", style="dim")
            client_section.append(f" [{subs} subs]", style="dim cyan")
            client_section.append("\n")
    else:
        client_section.append(
            "        ┊             (no clients connected)\n",
            style="dim",
        )

    # ── assemble diagram ──
    diagram = Text()
    diagram.append_text(oba_box)
    diagram.append("\n")
    diagram.append_text(oba_wire)
    diagram.append_text(server_box)
    diagram.append("\n")
    diagram.append_text(client_section)

    return Panel(
        diagram,
        title="[bold]Network Topology[/bold]",
        border_style="blue",
        padding=(1, 2),
    )


def render_trips(ms: MonitorState) -> Panel:
    """Show the most recent trip data sent to clients."""
    table = Table(
        show_header=True,
        header_style="bold",
        expand=True,
        box=None,
        padding=(0, 1),
    )
    table.add_column("Route", style="cyan", width=8)
    table.add_column("Headsign", style="white")
    table.add_column("Time", justify="right", width=6)
    table.add_column("RT", justify="center", width=3)
    table.add_column("Stop", style="dim", width=12)

    now = time.time()
    for t in ms.trips[:8]:
        at = t.get("arrivalTime", 0)
        if at > 10**12:
            at //= 1000
        wait = int((at - now) / 60) if at else -1
        time_str = "Now" if wait <= 0 else f"{wait}m"
        rt = "◉" if t.get("isRealtime") else "○"
        rt_style = "green" if t.get("isRealtime") else "dim"

        route_style = "cyan"
        color = t.get("routeColor")
        if color:
            try:
                route_style = f"#{color}" if len(color) == 6 else "cyan"
            except Exception:
                pass

        table.add_row(
            Text(t.get("routeName", "?"), style=route_style),
            t.get("headsign", ""),
            Text(time_str, style="bold"),
            Text(rt, style=rt_style),
            t.get("stopId", ""),
        )

    if not ms.trips:
        table.add_row(
            Text("—", style="dim"),
            Text("Waiting for data...", style="dim italic"),
            "", "", "",
        )

    return Panel(
        table,
        title="[bold]Last Schedule Push[/bold]",
        border_style="green",
        padding=(0, 1),
    )


def render_message_log(ms: MonitorState) -> Panel:
    """Scrolling log of recent messages between components."""
    log_text = Text()
    events = list(ms.events)
    # Show most recent events (bottom = newest)
    visible = events[-20:]
    if not visible:
        log_text.append(
            "  Waiting for activity...\n", style="dim italic"
        )
    for ev in visible:
        ts = time.strftime("%H:%M:%S", time.localtime(ev["ts"]))
        kind = ev["kind"]
        style = _KIND_STYLE.get(kind, "")
        direction = ev["direction"]
        detail = ev["detail"]

        log_text.append(f"  {ts} ", style="dim")
        log_text.append(f"{direction:<20s}", style=style)
        log_text.append(f" {detail}\n", style="")

    return Panel(
        log_text,
        title="[bold]Message Flow[/bold]",
        border_style="magenta",
        padding=(0, 1),
    )


def render_monitor(ms: MonitorState) -> Group:
    """Compose the full monitor display."""
    topology = render_topology(ms)
    trips = render_trips(ms)
    msg_log = render_message_log(ms)

    status_bar = Text()
    status_bar.append(" q", style="bold white on blue")
    status_bar.append(" quit  ", style="dim")
    status_bar.append(" m", style="bold white on blue")
    status_bar.append(" messages  ", style="dim")
    status_bar.append(
        f" Polling service_state.json every 1s ",
        style="dim italic",
    )

    return Group(
        Panel(
            Group(topology, trips, msg_log),
            title="[bold cyan]Transit Tracker — Live Monitor[/bold cyan]",
            border_style="cyan",
        ),
        status_bar,
    )


# ── main loop ───────────────────────────────────────────────────────


async def run_monitor(config: TransitConfig = None, use_ws: bool = False):
    """Run the live visual monitor.

    Polls ``service_state.json`` every second and renders a Rich Live
    display showing the network topology, recent trips, and message flow.
    """
    if config is None:
        path = get_last_config_path()
        if path and os.path.exists(path):
            config = TransitConfig.load(path)
        else:
            config = TransitConfig.load()

    console = Console()
    ms = MonitorState()

    # Initial state load
    state = get_service_state()
    if state:
        ms.ingest_state(state)

    log.info(
        "Starting live monitor", extra={"component": "monitor"}
    )

    ws = None
    ws_task = None

    async def _ws_listener():
        """Subscribe to local proxy for live messages."""
        import websockets

        nonlocal ws
        url = "ws://localhost:8000"
        while True:
            try:
                async with websockets.connect(url) as conn:
                    ws = conn
                    ms.ws_connected = True
                    ms._add_event(
                        "connect",
                        "monitor → server",
                        f"Subscribed to {url}",
                    )
                    # Send subscribe
                    sub_msg = json.dumps(
                        {
                            "event": "schedule:subscribe",
                            "client_name": "LiveMonitor",
                            "data": {
                                "routeStopPairs": build_route_stop_pairs(
                                    config.subscriptions
                                ),
                            },
                            "limit": 8,
                        }
                    )
                    await conn.send(sub_msg)
                    ms._add_event(
                        "subscribe",
                        "monitor → server",
                        f"Subscribed {len(config.subscriptions)} pairs",
                    )
                    async for raw in conn:
                        ms.ingest_ws_message(raw)
            except Exception as e:
                ms.ws_connected = False
                ms._add_event(
                    "disconnect",
                    "monitor ✕ server",
                    f"WS error: {e}",
                )
                await asyncio.sleep(5)

    if use_ws:
        ws_task = asyncio.create_task(_ws_listener())

    import select
    import sys
    import termios
    import tty

    old_settings = None
    try:
        # Set terminal to raw mode for key detection
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    except Exception:
        old_settings = None

    try:
        with Live(
            render_monitor(ms),
            console=console,
            refresh_per_second=1,
            screen=True,
        ) as live:
            while True:
                # Poll service state
                try:
                    state = get_service_state()
                    if state:
                        ms.ingest_state(state)
                except Exception:
                    pass

                live.update(render_monitor(ms))

                # Check for keypress (non-blocking)
                if old_settings is not None:
                    rlist, _, _ = select.select(
                        [sys.stdin], [], [], 0
                    )
                    if rlist:
                        ch = sys.stdin.read(1)
                        if ch in ("q", "Q", "\x03"):  # q or Ctrl+C
                            break

                await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        # Restore terminal
        if old_settings is not None:
            termios.tcsetattr(
                sys.stdin.fileno(),
                termios.TCSADRAIN,
                old_settings,
            )
        if ws_task:
            ws_task.cancel()

    log.info("Monitor stopped", extra={"component": "monitor"})
