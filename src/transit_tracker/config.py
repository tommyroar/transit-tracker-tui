import datetime
import os
import re
import tempfile
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SERVICE_SETTINGS_FILE = os.path.join(_PROJECT_ROOT, ".local", "service.yaml")

# Legacy path — read-only fallback for existing installs
_LEGACY_SETTINGS_DIR = os.path.expanduser("~/.config/transit-tracker")
_LEGACY_SETTINGS_FILE = os.path.join(_LEGACY_SETTINGS_DIR, "settings.yaml")


class DimmingEntry(BaseModel):
    time: str  # "HH:MM" format
    brightness: int = Field(ge=0, le=255)

    @field_validator("time")
    @classmethod
    def validate_time_format(cls, v):
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("time must be in HH:MM format")
        h, m = v.split(":")
        if not (0 <= int(h) <= 23 and 0 <= int(m) <= 59):
            raise ValueError("time must be valid HH:MM (00:00-23:59)")
        return v


class ServiceSettings(BaseModel):
    """Dev environment / service settings stored at .local/service.yaml.

    These are environment/instance concerns: credentials, polling intervals,
    hardware dimensions, and service mode flags.  They are NOT part of the
    board subscription schema and should never appear inside profile YAMLs.
    """

    last_config_path: Optional[str] = None

    # Credentials (env var OBA_API_KEY is still the primary fallback)
    oba_api_key: Optional[str] = None

    # Server polling / rate limiting
    check_interval_seconds: int = Field(default=30, ge=10)
    request_spacing_ms: int = Field(default=500, ge=0, le=2000)

    # Filtering
    arrival_threshold_minutes: int = Field(default=5, ge=1)

    # Hardware config
    num_panels: int = Field(default=2)
    panel_width: int = Field(default=64)
    panel_height: int = Field(default=32)

    # Service mode
    use_local_api: bool = Field(default=False)
    auto_launch_gui: bool = Field(default=True)

    # Display brightness / scheduled dimming
    display_brightness: int = Field(default=128, ge=0, le=255)
    device_ip: Optional[str] = None
    dimming_schedule: List[DimmingEntry] = Field(default_factory=list)


def _resolve_settings_path() -> str:
    """Return the path to the service settings file.

    Priority: SERVICE_SETTINGS_PATH env var (container override)
    Then: .local/service.yaml  (project-local, gitignored)
    Fallback: ~/.config/transit-tracker/settings.yaml  (legacy installs)
    """
    env_path = os.environ.get("SERVICE_SETTINGS_PATH")
    if env_path:
        return env_path
    if os.path.exists(SERVICE_SETTINGS_FILE):
        return SERVICE_SETTINGS_FILE
    if os.path.exists(_LEGACY_SETTINGS_FILE):
        return _LEGACY_SETTINGS_FILE
    return SERVICE_SETTINGS_FILE


def load_service_settings() -> ServiceSettings:
    """Load service settings from .local/service.yaml (or legacy fallback)."""
    path = _resolve_settings_path()
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f) or {}
                return ServiceSettings.model_validate(data)
        except Exception:
            pass
    return ServiceSettings()


def save_service_settings(settings: ServiceSettings):
    """Persist service settings to the resolved service.yaml path."""
    path = _resolve_settings_path()
    settings_dir = os.path.dirname(path)
    os.makedirs(settings_dir, exist_ok=True)
    data = settings.model_dump(exclude_none=True)
    fd, tmp_path = tempfile.mkstemp(dir=settings_dir, suffix=".yaml")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(data, f)
        os.replace(tmp_path, path)
    except Exception:
        os.unlink(tmp_path)
        raise


def get_last_config_path() -> Optional[str]:
    return load_service_settings().last_config_path


def set_last_config_path(path: str):
    svc = load_service_settings()
    svc.last_config_path = os.path.abspath(path)
    save_service_settings(svc)


def list_profiles() -> List[str]:
    """Lists available .yaml config files in project root and .local/."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    profiles = []

    # Check .local directory
    local_dir = os.path.join(project_root, ".local")
    if os.path.exists(local_dir):
        for f in os.listdir(local_dir):
            if f.endswith(".yaml") and f not in [
                "service_state.json",
                "accurate_config.yaml",
            ]:
                profiles.append(os.path.abspath(os.path.join(local_dir, f)))

    # Check project root
    for f in os.listdir(project_root):
        if f.endswith(".yaml") and f != "accurate_config.yaml":
            profiles.append(os.path.abspath(os.path.join(project_root, f)))

    # Include accurate_config.yaml if it exists
    acc_path_local = os.path.join(project_root, ".local", "accurate_config.yaml")
    acc_path_root = os.path.join(project_root, "accurate_config.yaml")

    if os.path.exists(acc_path_local):
        profiles.append(os.path.abspath(acc_path_local))
    elif os.path.exists(acc_path_root):
        profiles.append(os.path.abspath(acc_path_root))

    return sorted(list(set(profiles)))


class TransitSubscription(BaseModel):
    feed: str
    route: str
    stop: str
    label: str
    direction: Optional[str] = None
    time_offset: str = "0min"


class Abbreviation(BaseModel):
    original: str
    short: str


class TransitStop(BaseModel):
    stop_id: str
    time_offset: str = "0min"
    label: Optional[str] = None
    direction: Optional[str] = None
    routes: List[str] = Field(default_factory=list)

    @field_validator("time_offset")
    @classmethod
    def validate_offset(cls, v):
        if not re.match(r"^-?\d+min$", v):
            raise ValueError("time_offset must be in format like '5min' or '-2min'")
        return v


class TransitTrackerSettings(BaseModel):
    """Board subscription schema — matches the public reference project format.

    Profile YAML files contain only this block under the ``transit_tracker:`` key.
    """

    base_url: str = Field(default="wss://tt.horner.tj/")
    time_display: str = Field(default="arrival")
    scroll_headsigns: bool = Field(default=False)
    display_format: str = Field(default="{ROUTE}  {HEADSIGN}  {LIVE} {TIME}")
    stops: List[TransitStop] = Field(default_factory=list)
    abbreviations: List[Abbreviation] = Field(default_factory=list)


# Keys that used to live inside transit_tracker but now belong in ServiceSettings
_LEGACY_TT_KEYS = {
    "oba_api_key",
    "check_interval_seconds",
    "request_spacing_ms",
    "num_panels",
    "panel_width",
    "panel_height",
    "arrival_threshold_minutes",
    "display_brightness",
    "device_ip",
    "dimming_schedule",
}

# Keys that used to live at the root of the config YAML
_LEGACY_ROOT_KEYS = {"use_local_api", "auto_launch_gui"}

# Dead fields that should be silently stripped
_DEAD_KEYS = {"mapbox_access_token", "show_units", "list_mode", "styles"}


def _migrate_legacy_fields(data: dict, svc: ServiceSettings):
    """If an old-format profile YAML has service-level fields, absorb them."""
    tt_data = data.get("transit_tracker", {})

    for key in _LEGACY_TT_KEYS:
        if key in tt_data:
            current_default = ServiceSettings.model_fields[key].default
            if getattr(svc, key) == current_default:
                setattr(svc, key, tt_data.pop(key))
            else:
                tt_data.pop(key)

    for key in _LEGACY_ROOT_KEYS:
        if key in data:
            current_default = ServiceSettings.model_fields[key].default
            if getattr(svc, key) == current_default:
                setattr(svc, key, data.pop(key))
            else:
                data.pop(key)

    # Strip dead fields
    for key in _DEAD_KEYS:
        tt_data.pop(key, None)
        data.pop(key, None)


class TransitConfig(BaseModel):
    """Runtime composite: merges a board subscription profile with service settings."""

    # Board subscription (from profile YAML)
    transit_tracker: TransitTrackerSettings = Field(
        default_factory=TransitTrackerSettings
    )

    # Service settings (from .local/service.yaml)
    service: ServiceSettings = Field(default_factory=ServiceSettings)

    # Derived fields (computed, not persisted)
    api_url: str = ""
    subscriptions: List[TransitSubscription] = Field(default_factory=list)

    # Testing state (runtime only)
    mock_state: Optional[List[Dict[str, Any]]] = None
    captures: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_internal_state(self) -> "TransitConfig":
        tt = self.transit_tracker
        svc = self.service

        # Compute api_url
        if svc.use_local_api:
            self.api_url = "ws://Tommys-Mac-mini.local:8000/"
        elif not self.api_url or tt.base_url != "wss://tt.horner.tj/":
            self.api_url = tt.base_url

        # Build flattened subscriptions from stops
        self.subscriptions = []
        for stop in tt.stops:
            for route in stop.routes:
                feed = route.split(":")[0] if ":" in route else "st"
                self.subscriptions.append(
                    TransitSubscription(
                        feed=feed,
                        route=route,
                        stop=stop.stop_id,
                        label=stop.label or f"Route {route}",
                        direction=stop.direction,
                        time_offset=stop.time_offset,
                    )
                )
        return self

    @classmethod
    def load(
        cls, path: str = "config.yaml", service_settings: ServiceSettings | None = None
    ) -> "TransitConfig":
        if service_settings is None:
            service_settings = load_service_settings()

        if not os.path.exists(path):
            local_path = os.path.join(".local", path)
            if os.path.exists(local_path):
                path = local_path
            else:
                return cls(service=service_settings)

        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        # Backward compat: absorb any legacy service fields from old profile YAMLs
        _migrate_legacy_fields(data, service_settings)

        tt_data = data.get("transit_tracker", data)
        return cls(
            transit_tracker=TransitTrackerSettings.model_validate(tt_data),
            service=service_settings,
        )

    def save(self, path: str = "config.yaml") -> None:
        """Save only the board subscription profile (transit_tracker block)."""
        if not os.path.dirname(path) and os.path.exists(".local"):
            path = os.path.join(".local", path)

        data = {
            "transit_tracker": self.transit_tracker.model_dump(exclude_defaults=False)
        }
        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)


def evaluate_dimming_schedule(
    schedule: List[DimmingEntry], now_time: datetime.time
) -> Optional[int]:
    """Find the brightness from the most recent past schedule entry.

    Returns None if schedule is empty.
    Handles midnight wraparound: if current time is before all entries,
    uses the last entry from the sorted list (active from "yesterday").
    """
    if not schedule:
        return None

    sorted_entries = sorted(schedule, key=lambda e: e.time)
    # Walk backwards to find the latest entry at or before now
    result = None
    for entry in sorted_entries:
        entry_time = datetime.time(int(entry.time[:2]), int(entry.time[3:]))
        if entry_time <= now_time:
            result = entry.brightness
    # If no entry is at or before now, wrap around to the last entry
    if result is None:
        result = sorted_entries[-1].brightness
    return result
