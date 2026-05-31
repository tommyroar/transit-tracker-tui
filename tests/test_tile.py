"""Tests for tile.py — the shared trip-processing module.

Parity tests against BaseSimulator._process_trip (so the refactor is
behaviour-preserving) plus shape tests for build_stop_tile (the
Home Assistant-facing wrapper).
"""

import time

import pytest

from transit_tracker.config import (
    TransitConfig,
    TransitStop,
    TransitSubscription,
)
from transit_tracker.simulator import BaseSimulator
from transit_tracker.tile import _normalize_id, build_stop_tile, process_trip

pytestmark = pytest.mark.unit


class _TestSimulator(BaseSimulator):
    async def run(self):
        pass


def _make_sub(
    feed="st",
    route="st:40_100240",
    stop="st:1_8494",
    label="554",
    offset="0min",
):
    return TransitSubscription(
        feed=feed,
        route=route,
        stop=stop,
        label=label,
        time_offset=offset,
    )


def _make_sim(sub):
    cfg = TransitConfig()
    cfg.subscriptions = [sub]
    return _TestSimulator(cfg, force_live=True)


# -- Parity: tile.process_trip vs BaseSimulator._process_trip ----------------


class TestParityWithSimulator:
    """tile.process_trip must produce the same departure shape the simulator
    expects (minus base_time_ms, which the simulator strips)."""

    def test_basic_trip(self):
        sub = _make_sub()
        sim = _make_sim(sub)
        now_ts = int(time.time())
        now_ms = now_ts * 1000
        trip = {
            "tripId": "st:t1",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "routeName": "554",
            "headsign": "Downtown Seattle",
            "arrivalTime": now_ts + 600,
            "departureTime": now_ts + 630,
            "isRealtime": True,
            "routeColor": "2B376E",
        }
        sim_out = sim._process_trip(trip, now_ms)
        tile_out = process_trip(trip, sub, now_ms)
        assert tile_out is not None
        tile_out.pop("base_time_ms", None)
        tile_out["stop_id"] = "1_8494"
        assert sim_out == tile_out

    def test_ferry_vessel_mapping(self):
        sub = _make_sub(
            feed="wsf",
            route="95_73",
            stop="95_7",
            label="WSF",
        )
        sim = _make_sim(sub)
        now_ts = int(time.time())
        now_ms = now_ts * 1000
        trip = {
            "tripId": "wsf:t1",
            "routeId": "95_73",
            "stopId": "95_7",
            "routeName": "wsf",
            "headsign": "Bainbridge Island",
            "arrivalTime": now_ts + 1200,
            "isRealtime": True,
            "vehicleId": "95_28",  # → "Sealth"
        }
        sim_out = sim._process_trip(trip, now_ms)
        tile_out = process_trip(trip, sub, now_ms)
        assert sim_out["headsign"] == "Sealth"
        assert tile_out["headsign"] == "Sealth"

    def test_route14_hot_pink(self):
        sub = _make_sub(route="st:1_100039", stop="st:1_11920", label="14")
        sim = _make_sim(sub)
        now_ts = int(time.time())
        trip = {
            "tripId": "st:t1",
            "routeId": "st:1_100039",
            "stopId": "st:1_11920",
            "routeName": "14",
            "headsign": "Downtown",
            "arrivalTime": now_ts + 600,
            "routeColor": "FDB71A",
        }
        sim_out = sim._process_trip(trip, now_ts * 1000)
        tile_out = process_trip(trip, sub, now_ts * 1000)
        assert sim_out["color"] == "hot_pink"
        assert tile_out["color"] == "hot_pink"

    def test_no_trip_id_returns_none(self):
        sub = _make_sub()
        trip = {
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "arrivalTime": int(time.time()) + 600,
        }
        assert process_trip(trip, sub, int(time.time()) * 1000) is None

    def test_far_past_returns_none(self):
        sub = _make_sub()
        trip = {
            "tripId": "st:t1",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "arrivalTime": int(time.time()) - 7200,
        }
        assert process_trip(trip, sub, int(time.time()) * 1000) is None


# -- Shape: build_stop_tile ---------------------------------------------------


class TestBuildStopTile:
    def test_empty_trips_yields_empty_departures(self):
        stop = TransitStop(
            stop_id="st:1_8494",
            label="Issaquah TC",
            routes=["st:40_100240"],
        )
        sub = _make_sub()
        tile = build_stop_tile(
            stop,
            [sub],
            trips=[],
            current_time_ms=int(time.time()) * 1000,
        )
        assert tile["stop_id"] == "st:1_8494"
        assert tile["label"] == "Issaquah TC"
        assert tile["departures"] == []

    def test_sorts_by_eta_and_caps(self):
        stop = TransitStop(
            stop_id="st:1_8494",
            label="X",
            routes=["st:40_100240"],
        )
        sub = _make_sub()
        now_ts = int(time.time())
        trips = [
            {
                "tripId": f"t{i}",
                "routeId": "st:40_100240",
                "stopId": "st:1_8494",
                "routeName": "554",
                "headsign": "Downtown",
                "arrivalTime": now_ts + offset,
                "isRealtime": True,
            }
            for i, offset in enumerate([1800, 600, 1200, 300, 900])
        ]
        tile = build_stop_tile(
            stop,
            [sub],
            trips,
            current_time_ms=now_ts * 1000,
            limit=3,
        )
        assert len(tile["departures"]) == 3
        etas = [d["eta_minutes"] for d in tile["departures"]]
        assert etas == sorted(etas)
        assert etas[0] == 5  # 300s → 5 min

    def test_filters_other_stops(self):
        stop = TransitStop(
            stop_id="st:1_8494",
            label="X",
            routes=["st:40_100240"],
        )
        sub = _make_sub()
        now_ts = int(time.time())
        trips = [
            {
                "tripId": "t1",
                "routeId": "st:40_100240",
                "stopId": "st:1_9999",  # different stop
                "routeName": "554",
                "headsign": "Other",
                "arrivalTime": now_ts + 600,
            },
            {
                "tripId": "t2",
                "routeId": "st:40_100240",
                "stopId": "st:1_8494",
                "routeName": "554",
                "headsign": "Match",
                "arrivalTime": now_ts + 600,
            },
        ]
        tile = build_stop_tile(stop, [sub], trips, now_ts * 1000)
        assert [d["trip_id"] for d in tile["departures"]] == ["t2"]

    def test_dedupes_trip_ids(self):
        stop = TransitStop(
            stop_id="st:1_8494",
            label="X",
            routes=["st:40_100240"],
        )
        sub = _make_sub()
        now_ts = int(time.time())
        trip = {
            "tripId": "dup",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "routeName": "554",
            "headsign": "D",
            "arrivalTime": now_ts + 600,
        }
        tile = build_stop_tile(stop, [sub], [trip, trip], now_ts * 1000)
        assert len(tile["departures"]) == 1

    def test_ha_shape(self):
        stop = TransitStop(
            stop_id="st:1_8494",
            label="Westlake",
            routes=["st:40_100240"],
        )
        sub = _make_sub()
        now_ts = int(time.time())
        trip = {
            "tripId": "abc",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "routeName": "554",
            "headsign": "Downtown Seattle",
            "arrivalTime": now_ts + 600,
            "isRealtime": True,
            "routeColor": "2B376E",
        }
        tile = build_stop_tile(stop, [sub], [trip], now_ts * 1000)
        dep = tile["departures"][0]
        # Required keys an HA REST sensor / template card needs:
        assert set(dep.keys()) >= {
            "route",
            "headsign",
            "eta_minutes",
            "arrival_time",
            "is_realtime",
            "color",
            "trip_id",
        }
        assert dep["is_realtime"] is True
        assert dep["arrival_time"] == now_ts + 600


# -- _normalize_id sanity (we duplicated from simulator.normalize_id) --------


class TestNormalizeId:
    def test_strip_st(self):
        assert _normalize_id("st:1_8494") == "1_8494"

    def test_wsf(self):
        assert _normalize_id("wsf:7") == "95_7"

    def test_already_clean(self):
        assert _normalize_id("1_8494") == "1_8494"

    def test_empty(self):
        assert _normalize_id("") == ""
