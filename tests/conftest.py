"""Shared fixtures for Transit Tracker test suite.

Centralises mock objects, sample data, and helper classes used across
multiple test files to avoid duplication.
"""

import json
import time
from unittest.mock import MagicMock

import pytest

from transit_tracker.config import TransitSubscription

# ---------------------------------------------------------------------------
# MockWS — lightweight WebSocket stand-in
# ---------------------------------------------------------------------------


class MockWS:
    """Minimal WebSocket mock that records messages sent to it."""

    def __init__(self, remote_addr: str = "192.168.1.50"):
        self.sent: list[dict] = []
        self._remote_address = (remote_addr, 1234)

    async def send(self, msg: str) -> None:
        self.sent.append(json.loads(msg))

    @property
    def remote_address(self):
        return self._remote_address


# ---------------------------------------------------------------------------
# Sample trip data factories
# ---------------------------------------------------------------------------


def make_sample_trip(**overrides) -> dict:
    """Return a realistic OBA-style trip dict with sensible defaults."""
    now = int(time.time())
    base = {
        "tripId": "st:t1",
        "routeId": "st:40_100240",
        "stopId": "st:1_8494",
        "arrivalTime": now + 600,
        "departureTime": now + 600,
        "predictedArrivalTime": (now + 600) * 1000,
        "scheduledArrivalTime": (now + 600) * 1000,
        "predictedDepartureTime": (now + 630) * 1000,
        "scheduledDepartureTime": (now + 630) * 1000,
        "routeName": "554",
        "headsign": "Downtown Seattle",
        "isRealtime": True,
        "routeColor": "2B376E",
        "vehicleId": None,
        "arrivalEnabled": True,
        "departureEnabled": True,
    }
    base.update(overrides)
    return base


def make_sample_ferry_trip(**overrides) -> dict:
    """Return a realistic WSF ferry trip dict."""
    now = int(time.time())
    base = {
        "tripId": "95_73503142611",
        "routeId": "95_37",
        "stopId": "95_3",
        "arrivalTime": (now + 1200) * 1000,
        "departureTime": (now + 1200) * 1000,
        "scheduledArrivalTime": (now + 1200) * 1000,
        "scheduledDepartureTime": (now + 1200) * 1000,
        "predictedArrivalTime": None,
        "predictedDepartureTime": None,
        "tripHeadsign": "Bainbridge Island",
        "headsign": "Bainbridge Island",
        "routeName": "Seattle - Bainbridge Island",
        "isRealtime": False,
        "vehicleId": None,
        "departureEnabled": True,
        "arrivalEnabled": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Mock config fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config():
    """Standard mock config with one Sound Transit subscription."""
    config = MagicMock()
    config.subscriptions = [
        TransitSubscription(
            feed="st",
            route="st:40_100240",
            stop="st:1_8494",
            label="Route st:40_100240",
        )
    ]
    config.service = MagicMock()
    config.service.use_local_api = True
    config.service.auto_launch_gui = True
    config.service.arrival_threshold_minutes = 5
    config.service.check_interval_seconds = 30
    config.service.request_spacing_ms = 250
    config.service.oba_api_key = None
    config.service.display_brightness = 128
    config.service.device_ip = None
    config.service.dimming_schedule = []
    config.transit_tracker = MagicMock()
    config.transit_tracker.abbreviations = []
    config.transit_tracker.time_display = "arrival"
    return config


@pytest.fixture
def ferry_config():
    """Mock config with a WSF ferry subscription."""
    config = MagicMock()
    config.subscriptions = [
        TransitSubscription(feed="wsf", route="95_37", stop="95_3", label="SEA-BI")
    ]
    config.service = MagicMock()
    config.service.use_local_api = True
    config.service.auto_launch_gui = False
    config.service.arrival_threshold_minutes = 5
    config.service.check_interval_seconds = 30
    config.service.request_spacing_ms = 250
    config.service.oba_api_key = None
    config.service.display_brightness = 128
    config.service.device_ip = None
    config.service.dimming_schedule = []
    config.transit_tracker = MagicMock()
    config.transit_tracker.abbreviations = []
    config.transit_tracker.time_display = "arrival"
    return config
