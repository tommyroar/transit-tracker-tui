"""Configurable trip display formatting.

Maps API response fields to named template variables and renders
trip lines using str.format_map(). The default template produces
output identical to the legacy hardcoded format in gui.py.
"""

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
