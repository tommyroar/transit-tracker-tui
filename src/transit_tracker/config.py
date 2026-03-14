import os
import re
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

GLOBAL_SETTINGS_DIR = os.path.expanduser("~/.config/transit-tracker")
GLOBAL_SETTINGS_FILE = os.path.join(GLOBAL_SETTINGS_DIR, "settings.yaml")


def get_last_config_path() -> Optional[str]:
    if os.path.exists(GLOBAL_SETTINGS_FILE):
        try:
            with open(GLOBAL_SETTINGS_FILE, "r") as f:
                data = yaml.safe_load(f) or {}
                return data.get("last_config_path")
        except Exception:
            pass
    return None


def set_last_config_path(path: str):
    os.makedirs(GLOBAL_SETTINGS_DIR, exist_ok=True)
    data = {}
    if os.path.exists(GLOBAL_SETTINGS_FILE):
        try:
            with open(GLOBAL_SETTINGS_FILE, "r") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            pass
    data["last_config_path"] = os.path.abspath(path)
    with open(GLOBAL_SETTINGS_FILE, "w") as f:
        yaml.safe_dump(data, f)


def list_profiles() -> List[str]:
    """Lists all available .yaml configuration files in the project root and .local directory."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    profiles = []

    # Check .local directory
    local_dir = os.path.join(project_root, ".local")
    if os.path.exists(local_dir):
        for f in os.listdir(local_dir):
            if f.endswith(".yaml") and f not in ["service_state.json", "accurate_config.yaml"]:
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
    time_offset: str = "0min"


class Abbreviation(BaseModel):
    original: str
    short: str


class TransitTrackerSettings(BaseModel):
    base_url: str = Field(default="wss://tt.horner.tj/")
    time_display: str = Field(default="arrival")
    show_units: str = Field(default="short")
    list_mode: str = Field(default="sequential")
    scroll_headsigns: bool = Field(default=False)
    num_panels: int = Field(default=2)
    panel_width: int = Field(default=64)
    panel_height: int = Field(default=32)
    stops: List[Any] = Field(default_factory=list)
    styles: List[Dict[str, Any]] = Field(default_factory=list)
    abbreviations: List[Abbreviation] = Field(default_factory=list)
    mapbox_access_token: Optional[str] = None


class TransitStop(BaseModel):
    stop_id: str
    time_offset: str = "0min"
    label: Optional[str] = None
    routes: List[str] = Field(default_factory=list)

    @field_validator("time_offset")
    @classmethod
    def validate_offset(cls, v):
        if not re.match(r"^-?\d+min$", v):
            raise ValueError("time_offset must be in format like '5min' or '-2min'")
        return v


class TransitConfig(BaseModel):
    """
    Root configuration object.
    Automatically handles the nested 'transit_tracker' key from the public configurator.
    """

    # Application settings
    use_local_api: bool = Field(default=False)
    auto_launch_gui: bool = Field(default=True)

    # Core settings (nested)
    transit_tracker: TransitTrackerSettings = Field(default_factory=TransitTrackerSettings)

    # Internal flattened state (synced from transit_tracker)
    api_url: str = ""
    num_panels: int = 2
    panel_width: int = 64
    panel_height: int = 32
    time_display: str = "arrival"
    scroll_headsigns: bool = False
    subscriptions: List[TransitSubscription] = Field(default_factory=list)

    # Testing state
    mock_state: Optional[List[Dict[str, Any]]] = None
    captures: List[Dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def sync_internal_state(self) -> "TransitConfig":
        tt = self.transit_tracker
        if self.use_local_api:
            # Force local proxy URL if mode is enabled
            # We use .local hostname instead of localhost so flashed hardware can connect
            self.api_url = "ws://Tommys-Mac-mini.local:8000/"
        elif not self.api_url or tt.base_url != "wss://tt.horner.tj/":
            # Sync from transit_tracker if api_url is empty or if base_url was explicitly provided
            self.api_url = tt.base_url

        self.num_panels = tt.num_panels
        self.panel_width = tt.panel_width
        self.panel_height = tt.panel_height
        self.time_display = tt.time_display
        self.scroll_headsigns = tt.scroll_headsigns

        # Build flattened subscriptions
        self.subscriptions = []
        for stop in tt.stops:
            # Check if stop is a dict or TransitStop
            s_data = stop if isinstance(stop, TransitStop) else TransitStop.model_validate(stop)
            for route in s_data.routes:
                feed = route.split(":")[0] if ":" in route else "st"
                self.subscriptions.append(
                    TransitSubscription(
                        feed=feed,
                        route=route,
                        stop=s_data.stop_id,
                        label=s_data.label or f"Route {route}",
                        time_offset=s_data.time_offset,
                    )
                )
        return self

    @classmethod
    def load(cls, path: str = "config.yaml") -> "TransitConfig":
        if os.path.exists(path):
            pass
        else:
            local_path = os.path.join(".local", path)
            if os.path.exists(local_path):
                path = local_path
            else:
                return cls()

        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}

        # Ensure we always create a fresh instance with the data
        # If it only contains transit_tracker, Pydantic will handle it
        return cls.model_validate(data)

    def save(self, path: str = "config.yaml") -> None:
        if not os.path.dirname(path) and os.path.exists(".local"):
            path = os.path.join(".local", path)

        data = self.model_dump(exclude_unset=True)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
