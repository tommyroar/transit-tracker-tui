"""Transit Tracker web server package.

Re-exports the public API so that existing imports like
``from transit_tracker.web import run_web`` continue to work.
"""

from .api_handlers import (
    _get_draft,
    _handle_arrivals,
    _handle_config_save,
    _handle_config_settings_get,
    _handle_config_settings_patch,
    _handle_config_stops_delete,
    _handle_config_stops_get,
    _handle_config_stops_post,
    _handle_dimming_set,
    _handle_geocode,
    _handle_profile_activate,
    _handle_profiles_list,
    _handle_routes_for_location,
    _handle_stops_for_route,
    _reset_draft,
    resolve_stop_coordinates,
)
from .pages import (
    generate_dashboard_html,
    generate_index_html,
    generate_monitor_html,
    generate_simulator_html,
)
from .server import PREFIX, TransitWebHandler, run_web
from .spec import generate_api_spec, generate_spec_html

__all__ = [
    "PREFIX",
    "TransitWebHandler",
    "_get_draft",
    "_handle_arrivals",
    "_handle_config_save",
    "_handle_config_settings_get",
    "_handle_config_settings_patch",
    "_handle_config_stops_delete",
    "_handle_config_stops_get",
    "_handle_config_stops_post",
    "_handle_dimming_set",
    "_handle_geocode",
    "_handle_profile_activate",
    "_handle_profiles_list",
    "_handle_routes_for_location",
    "_handle_stops_for_route",
    "_reset_draft",
    "generate_api_spec",
    "generate_dashboard_html",
    "generate_index_html",
    "generate_monitor_html",
    "generate_simulator_html",
    "generate_spec_html",
    "resolve_stop_coordinates",
    "run_web",
]
