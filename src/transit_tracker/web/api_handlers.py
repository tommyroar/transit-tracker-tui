"""API endpoint handlers for the Transit Tracker web server.

Contains all request handler functions (sync and async) plus the
in-memory draft config state used by the stop-editing endpoints.
"""

import asyncio
import os
from typing import Any, Dict, List

from ..config import TransitConfig
from ..logging import get_logger
from ..transit_api import TransitAPI

log = get_logger("transit_tracker.web")


# -- Shared API helpers (used by both legacy HTTPServer and websockets paths) --


def _handle_profiles_list() -> dict:
    """Return profiles list and active profile as a dict."""
    from ..config import get_last_config_path, list_profiles

    active = get_last_config_path()
    profiles = [
        {"name": os.path.basename(p), "path": p, "active": p == active}
        for p in list_profiles()
    ]
    return {"profiles": profiles, "active": active}


def _handle_profile_activate(query: dict) -> tuple:
    """Activate a profile by name. Returns (status_code, response_dict)."""
    from ..config import list_profiles, set_last_config_path

    name = query.get("name", [None])[0]
    if not name:
        return (400, {"error": "Missing 'name' query parameter"})
    all_profiles = list_profiles()
    match = next(
        (p for p in all_profiles if os.path.basename(p) == name), None
    )
    if not match:
        available = [os.path.basename(p) for p in all_profiles]
        return (
            404,
            {"error": f"Profile '{name}' not found", "available": available},
        )
    log.info(
        "REST profile switch to %s",
        name,
        extra={"component": "web", "profile": match},
    )
    set_last_config_path(match)
    return (
        200,
        {
            "status": "ok",
            "profile": name,
            "path": match,
            "message": "Profile activated. Server will hot-reload within 30 seconds.",
        },
    )


def _handle_dimming_set(query: dict) -> tuple:
    """Update dimming settings from query params."""
    from ..config import (
        DimmingEntry,
        load_service_settings,
        save_service_settings,
    )

    log.info(
        "REST dimming update: %s",
        {k: v for k, v in query.items() if k != "device_ip"},
        extra={"component": "web"},
    )
    settings = load_service_settings()
    raw_entries = query.get("schedule", [])
    if raw_entries:
        entries = []
        for entry in raw_entries:
            time_str, brightness_str = entry.split(",", 1)
            entries.append(
                DimmingEntry(
                    time=time_str.strip(),
                    brightness=int(brightness_str.strip()),
                )
            )
        settings.dimming_schedule = entries
    if "brightness" in query:
        settings.display_brightness = int(query["brightness"][0])
    if "device_ip" in query:
        settings.device_ip = query["device_ip"][0]
    save_service_settings(settings)
    return (
        200,
        {
            "status": "ok",
            "dimming_schedule": [
                e.model_dump() for e in settings.dimming_schedule
            ],
            "display_brightness": settings.display_brightness,
            "device_ip": settings.device_ip,
            "message": "Dimming settings saved. Will take effect within 60 seconds.",
        },
    )


# -- In-memory draft config for stop/route editing via the web API --
# The draft is loaded from the active profile on first access and can be
# modified via POST/DELETE to /api/config/stops.  An explicit POST to
# /api/config/save persists the draft to disk.
_draft_config: TransitConfig | None = None
_draft_dirty: bool = False


def _get_draft() -> TransitConfig:
    """Return the in-memory draft config, loading from active profile if needed."""
    global _draft_config
    if _draft_config is None:
        from ..config import get_last_config_path

        path = get_last_config_path()
        if path and os.path.exists(path):
            _draft_config = TransitConfig.load(path)
        else:
            _draft_config = TransitConfig.load()
    return _draft_config


def _reset_draft():
    """Force-reload the draft from disk on next access."""
    global _draft_config, _draft_dirty
    _draft_config = None
    _draft_dirty = False


async def _handle_geocode(query: dict) -> tuple:
    """Geocode a location query. Returns (status, response_dict)."""
    q = query.get("q", [None])[0]
    if not q:
        return (400, {"error": "Missing 'q' query parameter"})
    api = TransitAPI()
    try:
        result = await api.geocode(q)
        if result is None:
            return (404, {"error": "Location not found"})
        lat, lon, display_name = result
        return (200, {"lat": lat, "lon": lon, "display_name": display_name})
    finally:
        await api.close()


async def _handle_routes_for_location(query: dict) -> tuple:
    """Find routes near a location. Returns (status, response_dict)."""
    lat = query.get("lat", [None])[0]
    lon = query.get("lon", [None])[0]
    if lat is None or lon is None:
        return (
            400,
            {"error": "Missing 'lat' and/or 'lon' query parameters"},
        )
    radius = int(query.get("radius", [1500])[0])
    api = TransitAPI()
    try:
        routes = await api.get_routes_for_location(
            float(lat), float(lon), radius
        )
        return (200, {"routes": routes})
    finally:
        await api.close()


async def _handle_stops_for_route(route_id: str) -> tuple:
    """Find stops for a route. Returns (status, response_dict)."""
    api = TransitAPI()
    try:
        stops = await api.get_stops_for_route(route_id)
        return (200, {"stops": stops})
    finally:
        await api.close()


async def _handle_arrivals(query: dict) -> tuple:
    """Get arrivals for a stop. Returns (status, response_dict)."""
    stop_id = query.get("stop_id", [None])[0]
    if not stop_id:
        return (400, {"error": "Missing 'stop_id' query parameter"})
    api = TransitAPI()
    try:
        arrivals = await api.get_arrivals(stop_id)
        return (200, {"arrivals": arrivals})
    finally:
        await api.close()


def _handle_config_stops_get() -> dict:
    """Return configured stops from the draft config."""
    draft = _get_draft()
    stops = []
    for stop in draft.transit_tracker.stops:
        stops.append(
            {
                "stop_id": stop.stop_id,
                "label": stop.label,
                "direction": stop.direction,
                "time_offset": stop.time_offset,
                "routes": stop.routes,
            }
        )
    return {"stops": stops, "dirty": _draft_dirty}


def _handle_config_stops_post(data: dict) -> tuple:
    """Add a stop subscription to the draft config."""
    global _draft_dirty
    from ..config import TransitStop

    draft = _get_draft()
    try:
        stop_id = data.get("stop_id")
        if not stop_id:
            return (400, {"error": "Missing 'stop_id'"})
        routes = data.get("routes", [])
        if isinstance(routes, str):
            routes = [routes]
        new_stop = TransitStop(
            stop_id=stop_id,
            label=data.get("label"),
            direction=data.get("direction"),
            time_offset=data.get("time_offset", "0min"),
            routes=routes,
        )
        draft.transit_tracker.stops.append(new_stop)
        _draft_dirty = True
        return (
            200,
            {
                "status": "ok",
                "message": "Stop added to draft",
                "stop": new_stop.model_dump(),
            },
        )
    except Exception as e:
        return (400, {"error": str(e)})


def _handle_config_stops_delete(data: dict) -> tuple:
    """Remove a stop subscription from the draft config."""
    global _draft_dirty
    draft = _get_draft()
    index = data.get("index")
    stop_id = data.get("stop_id")

    if index is not None:
        index = int(index)
        if 0 <= index < len(draft.transit_tracker.stops):
            removed = draft.transit_tracker.stops.pop(index)
            _draft_dirty = True
            return (
                200,
                {
                    "status": "ok",
                    "message": "Stop removed from draft",
                    "removed": removed.model_dump(),
                },
            )
        return (400, {"error": f"Index {index} out of range"})

    if stop_id:
        for i, s in enumerate(draft.transit_tracker.stops):
            if s.stop_id == stop_id:
                removed = draft.transit_tracker.stops.pop(i)
                _draft_dirty = True
                return (
                    200,
                    {
                        "status": "ok",
                        "message": "Stop removed from draft",
                        "removed": removed.model_dump(),
                    },
                )
        return (404, {"error": f"Stop '{stop_id}' not found in draft"})

    return (400, {"error": "Provide 'index' or 'stop_id'"})


def _handle_config_save(data: dict) -> tuple:
    """Save the draft config to disk."""
    global _draft_dirty
    draft = _get_draft()
    path = data.get("path")
    if path:
        draft.save(path)
    else:
        from ..config import get_last_config_path

        active = get_last_config_path()
        if active:
            draft.save(active)
        else:
            return (
                400,
                {"error": "No active profile and no path provided"},
            )
    _draft_dirty = False
    log.info(
        "Draft config saved to %s",
        path or active,
        extra={"component": "web"},
    )
    return (
        200,
        {"status": "ok", "message": f"Config saved to {path or active}"},
    )


def _handle_config_settings_get() -> dict:
    """Return current service settings."""
    from ..config import load_service_settings

    settings = load_service_settings()
    return settings.model_dump(exclude_none=True)


def _handle_config_settings_patch(data: dict) -> tuple:
    """Update service settings."""
    from ..config import load_service_settings, save_service_settings

    settings = load_service_settings()
    allowed = {
        "check_interval_seconds",
        "request_spacing_ms",
        "arrival_threshold_minutes",
        "num_panels",
        "panel_width",
        "panel_height",
        "use_local_api",
        "display_brightness",
        "device_ip",
    }
    updated = []
    for key, value in data.items():
        if key in allowed:
            setattr(settings, key, value)
            updated.append(key)
    if not updated:
        return (
            400,
            {"error": "No valid fields to update", "allowed": sorted(allowed)},
        )
    save_service_settings(settings)
    return (
        200,
        {"status": "ok", "updated": updated, "message": "Settings saved."},
    )


async def resolve_stop_coordinates(
    config: TransitConfig,
) -> List[Dict[str, Any]]:
    """Fetch lat/lon for all configured stops from the OBA API."""
    api = TransitAPI(oba_api_key=config.service.oba_api_key)
    try:
        tasks = [
            api.get_stop(stop.stop_id)
            for stop in config.transit_tracker.stops
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        stops = []
        for stop_cfg, result in zip(
            config.transit_tracker.stops, results, strict=True
        ):
            if isinstance(result, Exception):
                log.warning(
                    "Could not fetch stop %s: %s",
                    stop_cfg.stop_id,
                    result,
                    extra={"component": "web"},
                )
                continue
            if result is None:
                log.warning(
                    "Stop %s not found",
                    stop_cfg.stop_id,
                    extra={"component": "web"},
                )
                continue
            stops.append(
                {
                    "stop_id": stop_cfg.stop_id,
                    "name": result["name"],
                    "lat": result["lat"],
                    "lon": result["lon"],
                    "label": stop_cfg.label or result["name"],
                }
            )
        return stops
    finally:
        await api.close()
