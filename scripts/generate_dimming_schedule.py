#!/usr/bin/env python3
"""Generate a dimming schedule based on sunrise/sunset for Seattle.

Uses the `astral` library to compute sun times for the next 30 days,
then picks the earliest sunrise and latest sunset-minus-one-hour to
create a schedule that covers the full range. Writes the result to
.local/service.yaml.

Schedule logic:
  - Full brightness (255) at sunrise
  - 6 dimming steps over the hour before sunset (255 → ~40)
  - Off (0) at 22:00
  - Stays off until next sunrise
"""

import datetime
import os
import sys

import yaml
from astral import LocationInfo
from astral.sun import sun

# Seattle, WA — close enough for the Mac mini's location
CITY = LocationInfo("Seattle", "USA", "America/Los_Angeles", 47.6062, -122.3321)
DAYS_AHEAD = 30
FULL_BRIGHTNESS = 255
OFF_BRIGHTNESS = 0
SHUTOFF_TIME = datetime.time(22, 0)
DIMMING_STEPS = 6
DIMMING_WINDOW_MINUTES = 60

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SERVICE_YAML = os.path.join(PROJECT_ROOT, ".local", "service.yaml")


def compute_schedule():
    today = datetime.date.today()

    sunrises = []
    sunsets = []
    for i in range(DAYS_AHEAD):
        day = today + datetime.timedelta(days=i)
        s = sun(CITY.observer, date=day, tzinfo=CITY.timezone)
        sunrises.append(s["sunrise"])
        sunsets.append(s["sunset"])

    # Use the earliest sunrise (latest wake-up is covered) and the
    # earliest sunset (so dimming starts early enough for all 30 days).
    earliest_sunrise = min(sunrises)
    earliest_sunset = min(sunsets)

    print(f"Date range: {today} to {today + datetime.timedelta(days=DAYS_AHEAD - 1)}")
    print(f"Sunrise range: {min(sunrises).strftime('%H:%M')} – {max(sunrises).strftime('%H:%M')}")
    print(f"Sunset range:  {min(sunsets).strftime('%H:%M')} – {max(sunsets).strftime('%H:%M')}")
    print(f"Using: sunrise={earliest_sunrise.strftime('%H:%M')}, sunset={earliest_sunset.strftime('%H:%M')}")
    print()

    schedule = []

    # Sunrise → full brightness
    sunrise_hm = earliest_sunrise.strftime("%H:%M")
    schedule.append({"time": sunrise_hm, "brightness": FULL_BRIGHTNESS})

    # 6 dimming steps over the hour before the earliest sunset
    dim_start = earliest_sunset - datetime.timedelta(minutes=DIMMING_WINDOW_MINUTES)
    step_interval = DIMMING_WINDOW_MINUTES // DIMMING_STEPS
    for i in range(DIMMING_STEPS):
        t = dim_start + datetime.timedelta(minutes=step_interval * (i + 1))
        # Linear ramp from full brightness down to ~15% (40/255)
        frac = (i + 1) / DIMMING_STEPS
        brightness = int(FULL_BRIGHTNESS * (1 - frac * 0.85))
        schedule.append({"time": t.strftime("%H:%M"), "brightness": brightness})

    # 22:00 → off
    schedule.append({"time": "22:00", "brightness": OFF_BRIGHTNESS})

    return schedule


def main():
    schedule = compute_schedule()

    print("Generated schedule:")
    for entry in schedule:
        label = "OFF" if entry["brightness"] == 0 else f"{entry['brightness']}/255"
        print(f"  {entry['time']}  →  {label}")

    # Load existing service.yaml
    if os.path.exists(SERVICE_YAML):
        with open(SERVICE_YAML) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    data["dimming_schedule"] = schedule

    with open(SERVICE_YAML, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)

    print(f"\nWritten to {SERVICE_YAML}")


if __name__ == "__main__":
    main()
