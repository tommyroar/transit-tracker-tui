import yaml
import os
import re
from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field, model_validator, field_validator

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

class TransitSubscription(BaseModel):
    feed: str
    route: str
    stop: str
    label: str
    direction: Optional[int] = None
    time_offset: str = "0min"

class TransitStop(BaseModel):
    stop_id: str
    time_offset: str = "0min"
    label: Optional[str] = None
    routes: List[str] = Field(default_factory=list)

    @field_validator("time_offset")
    @classmethod
    def validate_offset(cls, v: str) -> str:
        if not re.match(r"^-?\d+(min|m|s)?$", v.lower()):
            raise ValueError("Offset must be a string like '5min', '-10m', or '30s'")
        return v

class RouteStyle(BaseModel):
    route_id: str
    name: Optional[str] = None
    color: Optional[str] = None

class Abbreviation(BaseModel):
    original: str
    short: str

class TransitTrackerSettings(BaseModel):
    base_url: str = Field(default="wss://tt.horner.tj/")
    time_display: str = Field(default="arrival")
    show_units: str = Field(default="long") # "long", "short", "none"
    list_mode: str = Field(default="sequential") # "sequential", "next_arrival"
    scroll_headsigns: bool = Field(default=False)
    stops: List[TransitStop] = Field(default_factory=list)
    styles: List[RouteStyle] = Field(default_factory=list)
    abbreviations: List[Abbreviation] = Field(default_factory=list)
    
    # Hardware defaults for the physical board
    num_panels: int = Field(default=2, ge=1, le=4)
    panel_width: int = Field(default=64, ge=32)
    panel_height: int = Field(default=32, ge=16)

class TransitConfig(BaseModel):
    """
    Unified Configuration for both the TUI wrapper and the core simulator.
    Automatically handles the nested 'transit_tracker' key from the public configurator.
    """
    # Application settings
    use_local_api: bool = Field(default=False)
    auto_launch_gui: bool = Field(default=True)
    ntfy_topic: str = Field(default="transit-alerts")
    arrival_threshold_minutes: int = Field(default=5, ge=1)
    check_interval_seconds: int = Field(default=30, ge=10)
    
    # Configurator data
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
            for route in stop.routes:
                feed = route.split(":")[0] if ":" in route else "st"
                self.subscriptions.append(TransitSubscription(
                    feed=feed,
                    route=route,
                    stop=stop.stop_id,
                    label=stop.label or f"Route {route}",
                    time_offset=stop.time_offset
                ))
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
