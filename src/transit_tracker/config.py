import yaml
import os
from typing import List, Optional, Union, Dict, Any
from pydantic import BaseModel, Field, model_validator

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
    time_offset: Optional[str] = None

class TransitStop(BaseModel):
    stop_id: str
    time_offset: str = "0min"
    routes: List[str] = Field(default_factory=list)

class TransitTrackerSettings(BaseModel):
    base_url: str = "wss://tt.horner.tj/"
    time_display: str = "arrival"
    num_panels: int = 1
    scroll_headsigns: bool = True
    stops: List[TransitStop] = Field(default_factory=list)

class TransitConfig(BaseModel):
    # Compatibility with flat format
    api_url: str = Field(default="wss://tt.horner.tj")
    ntfy_topic: str = Field(default="transit-alerts")
    arrival_threshold_minutes: int = Field(default=5, ge=1)
    check_interval_seconds: int = Field(default=30, ge=10)
    num_panels: int = Field(default=1, ge=1, le=4)
    time_display: str = Field(default="arrival")
    scroll_headsigns: bool = Field(default=True)
    subscriptions: List[TransitSubscription] = Field(default_factory=list)
    
    # Mocking for Testing
    mock_state: Optional[List[Dict[str, Any]]] = None
    
    # Compatibility with nested format
    transit_tracker: Optional[TransitTrackerSettings] = None

    @model_validator(mode="after")
    def migrate_nested_config(self) -> "TransitConfig":
        if self.transit_tracker:
            if hasattr(self.transit_tracker, 'base_url'):
                self.api_url = self.transit_tracker.base_url
            if hasattr(self.transit_tracker, 'time_display'):
                self.time_display = self.transit_tracker.time_display
            if hasattr(self.transit_tracker, 'num_panels'):
                self.num_panels = self.transit_tracker.num_panels
            if hasattr(self.transit_tracker, 'scroll_headsigns'):
                self.scroll_headsigns = self.transit_tracker.scroll_headsigns
            
            if self.transit_tracker.stops:
                for stop in self.transit_tracker.stops:
                    for route in stop.routes:
                        exists = any(s.stop == stop.stop_id and s.route == route for s in self.subscriptions)
                        if not exists:
                            agency_id = route.split("_")[0] if "_" in route else ""
                            feed = "st" if agency_id == "40" else "kcm" if agency_id == "1" else "st"
                            self.subscriptions.append(TransitSubscription(
                                feed=feed,
                                route=route,
                                stop=stop.stop_id,
                                label=f"Route {route}",
                                time_offset=stop.time_offset
                            ))
        return self

    @classmethod
    def load(cls, path: str = "config.yaml") -> "TransitConfig":
        if not os.path.exists(path):
            return cls()
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)

    def save(self, path: str = "config.yaml") -> None:
        data = self.model_dump(exclude_unset=True)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
