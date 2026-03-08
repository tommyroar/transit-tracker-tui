import yaml
import os
from typing import List, Optional
from pydantic import BaseModel, Field

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
    time_offset: Optional[str] = None  # e.g., "-7min" or seconds

class TransitConfig(BaseModel):
    api_url: str = Field(default="wss://tt.horner.tj")
    ntfy_topic: str = Field(default="transit-alerts")
    arrival_threshold_minutes: int = Field(default=5, ge=1)
    check_interval_seconds: int = Field(default=30, ge=10)
    num_panels: int = Field(default=1, ge=1, le=4)
    subscriptions: List[TransitSubscription] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str = "config.yaml") -> "TransitConfig":
        if not os.path.exists(path):
            return cls()
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        return cls(**data)

    def save(self, path: str = "config.yaml") -> None:
        # Convert to dict, exclude unset optional values
        data = self.model_dump(exclude_unset=True)
        with open(path, "w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
