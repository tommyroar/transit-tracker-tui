"""Configurable trip display formatting.

Maps API response fields to named template variables and renders
trip lines using str.format_map(). The default template produces
output identical to the legacy hardcoded format in gui.py.

For bitmap rendering (LED simulator), use build_bitmap_segments()
to parse the template into ordered segments with variable names,
resolved text, and color roles that the renderer can map to fonts
and colors independently.
"""

import re

DISPLAY_VARIABLES = {
    "ROUTE": "Route short name (e.g. '554')",
    "HEADSIGN": "Trip destination (e.g. 'Downtown Seattle')",
    "LIVE": "Realtime indicator: \u25c9 (realtime) or \u25cb (scheduled)",
    "TIME": "Wait time: 'Now' or 'Xm'",
    "WAIT": "Raw wait minutes as integer string",
    "ROUTE_ID": "Full route ID (e.g. '40_100240')",
    "STOP_ID": "Stop ID (e.g. '1_8494')",
    "ROUTE_COLOR": "Route color hex string (e.g. 'FF00FF')",
}

DEFAULT_DISPLAY_FORMAT = "{ROUTE}  {HEADSIGN}  {LIVE} {TIME}"


def build_trip_variables(trip: dict, now: float) -> dict:
    """Compute all template variables from a trip dict and current time."""
    route = trip.get("routeName", "?")
    headsign = trip.get("headsign", "")
    at = trip.get("arrivalTime", 0)
    if at and at > 10**12:
        at //= 1000
    wait = int((at - now) / 60) if at else -1
    time_str = "Now" if wait <= 0 else f"{wait}m"
    rt = "\u25c9" if trip.get("isRealtime") else "\u25cb"

    return {
        "ROUTE": route,
        "HEADSIGN": headsign,
        "LIVE": rt,
        "TIME": time_str,
        "WAIT": str(wait),
        "ROUTE_ID": trip.get("routeId", ""),
        "STOP_ID": trip.get("stopId", ""),
        "ROUTE_COLOR": trip.get("routeColor") or "",
    }


def format_trip_line(trip: dict, now: float, fmt: str | None = None) -> str:
    """Format a single trip dict using a template string.

    Uses str.format_map() with named variables from DISPLAY_VARIABLES.
    Falls back to DEFAULT_DISPLAY_FORMAT if *fmt* is None or malformed.
    """
    if fmt is None:
        fmt = DEFAULT_DISPLAY_FORMAT
    variables = build_trip_variables(trip, now)
    try:
        return fmt.format_map(variables)
    except (KeyError, ValueError, IndexError):
        return DEFAULT_DISPLAY_FORMAT.format_map(variables)


# Pattern to split "{VAR}" tokens from literal text
_SEGMENT_RE = re.compile(r"\{([A-Z_]+)\}")


def build_bitmap_segments(
    dep: dict, fmt: str | None = None
) -> list[dict]:
    """Parse a display format into ordered bitmap segments.

    Each segment dict has:
        variable: str | None  — variable name or None for literal
        text: str             — resolved text to render
        role: str             — color role for the renderer

    The simulator uses these segments to render bitmaps left-to-right
    with per-segment color and special handling (e.g. LIVE icon).

    *dep* uses the simulator's internal departure dict format
    (keys: route, headsign, diff, live, color, stop_id).
    """
    if fmt is None:
        fmt = DEFAULT_DISPLAY_FORMAT

    # Resolve variable values from the simulator dep dict
    diff = dep.get("diff", 0)
    values = {
        "ROUTE": str(dep.get("route", "?")),
        "HEADSIGN": dep.get("headsign", ""),
        "LIVE": "",  # handled as icon by renderer
        "TIME": "Now" if diff <= 0 else f"{diff}m",
        "WAIT": str(diff),
        "ROUTE_ID": dep.get("route_id", ""),
        "STOP_ID": dep.get("stop_id", ""),
        "ROUTE_COLOR": dep.get("color", ""),
    }

    # Color roles per variable
    route_color = dep.get("color", "yellow")
    is_live = dep.get("live", False)
    roles = {
        "ROUTE": route_color,
        "HEADSIGN": "white",
        "LIVE": "live_icon",
        "TIME": "bright_blue" if is_live else "grey74",
        "WAIT": "bright_blue" if is_live else "grey74",
        "ROUTE_ID": route_color,
        "STOP_ID": "white",
        "ROUTE_COLOR": "white",
    }

    segments: list[dict] = []
    pos = 0
    for m in _SEGMENT_RE.finditer(fmt):
        # Literal text before this variable
        if m.start() > pos:
            literal = fmt[pos : m.start()]
            segments.append(
                {"variable": None, "text": literal, "role": "white"}
            )
        var = m.group(1)
        segments.append({
            "variable": var,
            "text": values.get(var, ""),
            "role": roles.get(var, "white"),
        })
        pos = m.end()

    # Trailing literal
    if pos < len(fmt):
        segments.append(
            {"variable": None, "text": fmt[pos:], "role": "white"}
        )

    return segments
