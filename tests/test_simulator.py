"""Comprehensive tests for the refactored simulator base class."""

import time

from transit_tracker.config import TransitConfig, TransitSubscription
from transit_tracker.simulator import BaseSimulator, MicroFont, TUISimulator


# Concrete subclass for testing (BaseSimulator is abstract)
class _TestSimulator(BaseSimulator):
    async def run(self):
        pass


def _make_config_with_subs(subs=None):
    config = TransitConfig()
    if subs:
        config.subscriptions = subs
    return config


def _make_sub(feed="st", route="40_100240", stop="1_8494", label="554", offset="-7min"):
    return TransitSubscription(
        feed=feed,
        route=route,
        stop=stop,
        label=label,
        time_offset=offset,
    )


# ---------------------------------------------------------------------------
# normalize_id
# ---------------------------------------------------------------------------


class TestNormalizeId:
    def test_strip_st_prefix(self):
        assert BaseSimulator.normalize_id("st:1_8494") == "1_8494"

    def test_strip_st_prefix_route(self):
        assert BaseSimulator.normalize_id("st:40_100240") == "40_100240"

    def test_wsf_prefix(self):
        assert BaseSimulator.normalize_id("wsf:7") == "95_7"

    def test_wsf_prefix_double_digit(self):
        assert BaseSimulator.normalize_id("wsf:73") == "95_73"

    def test_no_prefix(self):
        assert BaseSimulator.normalize_id("1_8494") == "1_8494"

    def test_empty(self):
        assert BaseSimulator.normalize_id("") == ""

    def test_none_like(self):
        assert BaseSimulator.normalize_id("") == ""

    def test_plain_number(self):
        assert BaseSimulator.normalize_id("12345") == "12345"

    def test_colon_without_underscore(self):
        # "foo:bar" has colon but no underscore — returned as-is
        assert BaseSimulator.normalize_id("foo:bar") == "foo:bar"


# ---------------------------------------------------------------------------
# build_subscribe_payload
# ---------------------------------------------------------------------------


class TestBuildSubscribePayload:
    def test_basic_payload(self):
        config = _make_config_with_subs([_make_sub()])
        sim = _TestSimulator(config, force_live=True)
        payload = sim.build_subscribe_payload()

        assert payload["event"] == "schedule:subscribe"
        assert payload["client_name"] == "Simulator"
        assert payload["data"]["limit"] == 10

        pairs = payload["data"]["routeStopPairs"]
        # Should contain route,stop,offset_seconds
        assert "40_100240" in pairs
        assert "1_8494" in pairs

    def test_offset_conversion(self):
        config = _make_config_with_subs([_make_sub(offset="-7min")])
        sim = _TestSimulator(config, force_live=True)
        payload = sim.build_subscribe_payload()
        pairs = payload["data"]["routeStopPairs"]
        # -7min = -420 seconds
        assert "-420" in pairs

    def test_zero_offset(self):
        config = _make_config_with_subs([_make_sub(offset="0min")])
        sim = _TestSimulator(config, force_live=True)
        payload = sim.build_subscribe_payload()
        pairs = payload["data"]["routeStopPairs"]
        assert pairs.endswith(",0")

    def test_multiple_subs(self):
        config = _make_config_with_subs(
            [
                _make_sub(route="40_100240", stop="1_8494", offset="-7min"),
                _make_sub(
                    feed="st",
                    route="1_100039",
                    stop="1_11920",
                    label="14",
                    offset="-9min",
                ),
            ]
        )
        sim = _TestSimulator(config, force_live=True)
        payload = sim.build_subscribe_payload()
        pairs = payload["data"]["routeStopPairs"]
        assert ";" in pairs  # Multiple pairs separated by semicolon

    def test_custom_client_name(self):
        config = _make_config_with_subs([_make_sub()])
        sim = _TestSimulator(config, force_live=True)
        payload = sim.build_subscribe_payload(client_name="TestClient", limit=5)
        assert payload["client_name"] == "TestClient"
        assert payload["data"]["limit"] == 5

    def test_prefixed_route_ids(self):
        """Routes already containing ':' should not get double-prefixed."""
        sub = TransitSubscription(
            feed="st",
            route="st:40_100240",
            stop="st:1_8494",
            label="554",
            time_offset="0min",
        )
        config = _make_config_with_subs([sub])
        sim = _TestSimulator(config, force_live=True)
        payload = sim.build_subscribe_payload()
        pairs = payload["data"]["routeStopPairs"]
        # Should use the route as-is since it already has ":"
        assert "st:40_100240" in pairs
        assert "st:st:" not in pairs


# ---------------------------------------------------------------------------
# resolve_ws_url
# ---------------------------------------------------------------------------


class TestResolveWsUrl:
    def test_default_cloud(self):
        config = TransitConfig()
        sim = _TestSimulator(config, force_live=True)
        url = sim.resolve_ws_url()
        assert "tt.horner.tj" in url

    def test_local_api(self):
        config = TransitConfig()
        config.service.use_local_api = True
        config.api_url = "ws://localhost:8000/"
        sim = _TestSimulator(config, force_live=True)
        url = sim.resolve_ws_url()
        assert url == "ws://localhost:8000"

    def test_local_api_fallback(self):
        """When use_local_api but api_url is not localhost, force localhost."""
        config = TransitConfig()
        config.service.use_local_api = True
        config.api_url = "ws://somehost:8000/"
        sim = _TestSimulator(config, force_live=True)
        url = sim.resolve_ws_url()
        assert url == "ws://localhost:8000"

    def test_strips_trailing_slash(self):
        config = TransitConfig()
        config.api_url = "wss://tt.horner.tj/"
        sim = _TestSimulator(config, force_live=True)
        url = sim.resolve_ws_url()
        assert not url.endswith("/")


# ---------------------------------------------------------------------------
# _process_trip
# ---------------------------------------------------------------------------


class TestProcessTrip:
    def _make_sim(self, offset="0min"):
        config = _make_config_with_subs([_make_sub(offset=offset)])
        return _TestSimulator(config, force_live=True)

    def test_basic_trip(self):
        sim = self._make_sim()
        now_ts = int(time.time())
        now_ms = now_ts * 1000
        trip = {
            "tripId": "st:t1",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "routeName": "554",
            "headsign": "Downtown Seattle",
            "arrivalTime": now_ts + 600,  # 10 min from now (seconds)
            "departureTime": now_ts + 630,
            "isRealtime": True,
            "routeColor": "2B376E",
        }
        dep = sim._process_trip(trip, now_ms)
        assert dep is not None
        assert dep["trip_id"] == "st:t1"
        assert dep["route"] == "554"
        assert dep["headsign"] == "Downtown Seattle"
        assert dep["diff"] == 10
        assert dep["live"] is True
        assert dep["color"] == "#2B376E"

    def test_trip_no_matching_sub(self):
        sim = self._make_sim()
        now_ms = int(time.time()) * 1000
        trip = {
            "tripId": "st:t1",
            "routeId": "st:99_999",
            "stopId": "st:1_9999",
            "arrivalTime": int(time.time()) + 600,
            "routeName": "999",
        }
        assert sim._process_trip(trip, now_ms) is None

    def test_trip_no_trip_id(self):
        sim = self._make_sim()
        now_ms = int(time.time()) * 1000
        trip = {
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "arrivalTime": int(time.time()) + 600,
        }
        assert sim._process_trip(trip, now_ms) is None

    def test_trip_far_in_past(self):
        sim = self._make_sim()
        now_ms = int(time.time()) * 1000
        trip = {
            "tripId": "st:t1",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "arrivalTime": int(time.time()) - 7200,  # 2 hours ago
            "routeName": "554",
        }
        assert sim._process_trip(trip, now_ms) is None

    def test_ferry_vessel_mapping(self):
        """Ferry trips with vehicleId should get vessel name as headsign."""
        sub = _make_sub(feed="wsf", route="95_73", stop="95_7", label="WSF")
        config = _make_config_with_subs([sub])
        sim = _TestSimulator(config, force_live=True)
        now_ms = int(time.time()) * 1000
        trip = {
            "tripId": "wsf:t1",
            "routeId": "95_73",
            "stopId": "95_7",
            "routeName": "wsf",
            "headsign": "Bainbridge Island",
            "arrivalTime": int(time.time()) + 1200,
            "isRealtime": True,
            "vehicleId": "95_28",  # Should map to "Sealth"
        }
        dep = sim._process_trip(trip, now_ms)
        assert dep is not None
        assert dep["headsign"] == "Sealth"

    def test_negative_diff_clamped_to_zero(self):
        sim = self._make_sim()
        now_ms = int(time.time()) * 1000
        trip = {
            "tripId": "st:t1",
            "routeId": "st:40_100240",
            "stopId": "st:1_8494",
            "arrivalTime": int(time.time()) - 30,  # 30s ago
            "routeName": "554",
            "headsign": "Downtown",
        }
        dep = sim._process_trip(trip, now_ms)
        assert dep is not None
        assert dep["diff"] == 0

    def test_route14_gets_hot_pink(self):
        sub = _make_sub(route="1_100039", stop="1_11920", label="14")
        config = _make_config_with_subs([sub])
        sim = _TestSimulator(config, force_live=True)
        now_ms = int(time.time()) * 1000
        trip = {
            "tripId": "st:t1",
            "routeId": "st:1_100039",
            "stopId": "st:1_11920",
            "routeName": "14",
            "headsign": "Downtown",
            "arrivalTime": int(time.time()) + 600,
            "routeColor": "FDB71A",
        }
        dep = sim._process_trip(trip, now_ms)
        assert dep["color"] == "hot_pink"


# ---------------------------------------------------------------------------
# _apply_diversity_cap
# ---------------------------------------------------------------------------


class TestDiversityCap:
    def test_single_stop(self):
        deps = [
            {"diff": 2, "stop_id": "A"},
            {"diff": 5, "stop_id": "A"},
            {"diff": 8, "stop_id": "A"},
            {"diff": 12, "stop_id": "A"},
        ]
        result = BaseSimulator._apply_diversity_cap(deps, limit=3)
        assert len(result) == 3
        assert [d["diff"] for d in result] == [2, 5, 8]

    def test_two_stops_fair(self):
        deps = [
            {"diff": 1, "stop_id": "A"},
            {"diff": 2, "stop_id": "A"},
            {"diff": 3, "stop_id": "B"},
            {"diff": 4, "stop_id": "A"},
        ]
        result = BaseSimulator._apply_diversity_cap(deps, limit=3)
        assert len(result) == 3
        # Stop A gets slot 1, Stop B gets slot 2, then fill
        stop_ids = [d["stop_id"] for d in result]
        assert "A" in stop_ids
        assert "B" in stop_ids

    def test_three_stops(self):
        deps = [
            {"diff": 5, "stop_id": "A"},
            {"diff": 3, "stop_id": "B"},
            {"diff": 1, "stop_id": "C"},
        ]
        result = BaseSimulator._apply_diversity_cap(deps, limit=3)
        assert len(result) == 3
        # All three stops get one slot each
        assert {d["stop_id"] for d in result} == {"A", "B", "C"}

    def test_empty(self):
        assert BaseSimulator._apply_diversity_cap([], limit=3) == []

    def test_fewer_than_limit(self):
        deps = [{"diff": 1, "stop_id": "A"}]
        result = BaseSimulator._apply_diversity_cap(deps, limit=3)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# get_upcoming_departures
# ---------------------------------------------------------------------------


class TestGetUpcomingDepartures:
    def test_mock_state(self):
        config = TransitConfig()
        sim = _TestSimulator(config, force_live=False)
        sim.state = {
            "mock": {
                "trips": [
                    {
                        "route": "554",
                        "headsign": "Downtown",
                        "diff": 5,
                        "live": True,
                        "color": "yellow",
                    },
                    {
                        "route": "14",
                        "headsign": "CD",
                        "diff": 2,
                        "live": False,
                        "color": "hot_pink",
                    },
                ],
                "timestamp": time.time(),
            }
        }
        deps = sim.get_upcoming_departures()
        assert len(deps) == 2
        assert deps[0]["route"] == "14"  # Sorted by diff
        assert deps[1]["route"] == "554"

    def test_live_state_sorts_by_diff(self):
        config = _make_config_with_subs(
            [
                _make_sub(route="40_100240", stop="1_8494", offset="0min"),
                _make_sub(route="1_100039", stop="1_11920", label="14", offset="0min"),
            ]
        )
        sim = _TestSimulator(config, force_live=True)
        now_ts = int(time.time())
        sim.state = {
            "live": {
                "trips": [
                    {
                        "tripId": "t1",
                        "routeId": "st:40_100240",
                        "stopId": "st:1_8494",
                        "routeName": "554",
                        "headsign": "Downtown",
                        "arrivalTime": now_ts + 900,
                        "isRealtime": True,
                    },
                    {
                        "tripId": "t2",
                        "routeId": "st:1_100039",
                        "stopId": "st:1_11920",
                        "routeName": "14",
                        "headsign": "CD",
                        "arrivalTime": now_ts + 300,
                        "isRealtime": False,
                    },
                ],
                "timestamp": now_ts,
            }
        }
        deps = sim.get_upcoming_departures()
        assert len(deps) == 2
        assert deps[0]["route"] == "14"  # 5 min
        assert deps[1]["route"] == "554"  # 15 min

    def test_stale_live_data_ignored(self):
        config = _make_config_with_subs([_make_sub()])
        sim = _TestSimulator(config, force_live=True)
        sim.state = {
            "live": {
                "trips": [
                    {
                        "tripId": "t1",
                        "routeId": "st:40_100240",
                        "stopId": "st:1_8494",
                        "arrivalTime": int(time.time()) + 600,
                        "routeName": "554",
                    }
                ],
                "timestamp": time.time() - 700,  # 700s old > 600s threshold
            }
        }
        deps = sim.get_upcoming_departures()
        assert len(deps) == 0

    def test_dedup_by_trip_id(self):
        config = _make_config_with_subs([_make_sub()])
        sim = _TestSimulator(config, force_live=True)
        now_ts = int(time.time())
        sim.state = {
            "live": {
                "trips": [
                    {
                        "tripId": "t1",
                        "routeId": "st:40_100240",
                        "stopId": "st:1_8494",
                        "arrivalTime": now_ts + 600,
                        "routeName": "554",
                        "headsign": "A",
                    },
                    {
                        "tripId": "t1",
                        "routeId": "st:40_100240",
                        "stopId": "st:1_8494",
                        "arrivalTime": now_ts + 600,
                        "routeName": "554",
                        "headsign": "A",
                    },
                ],
                "timestamp": now_ts,
            }
        }
        deps = sim.get_upcoming_departures()
        assert len(deps) == 1


# ---------------------------------------------------------------------------
# _parse_capture
# ---------------------------------------------------------------------------


class TestParseCapture:
    def test_basic_capture(self):
        capture = {"display": "554  Downtown  {LIVE} 8m\n14  Central District  3m"}
        result = BaseSimulator._parse_capture(capture)
        assert len(result) == 2
        assert result[0]["route"] == "554"
        assert result[0]["live"] is True
        assert result[0]["diff"] == 8
        assert result[1]["route"] == "14"
        assert result[1]["live"] is False
        assert result[1]["diff"] == 3


# ---------------------------------------------------------------------------
# get_current_display_text
# ---------------------------------------------------------------------------


class TestDisplayText:
    def test_display_text_format(self):
        config = TransitConfig()
        sim = _TestSimulator(config, force_live=False)
        sim.state = {
            "mock": {
                "trips": [
                    {
                        "route": "554",
                        "headsign": "Downtown",
                        "diff": 8,
                        "live": True,
                        "color": "yellow",
                    },
                ],
                "timestamp": time.time(),
            }
        }
        text = sim.get_current_display_text()
        assert "554" in text
        assert "8m" in text
        assert "{LIVE}" in text  # Live trip has live indicator


# ---------------------------------------------------------------------------
# MicroFont
# ---------------------------------------------------------------------------


class TestMicroFont:
    def test_bitmap_dimensions(self):
        bm = MicroFont.get_bitmap("A")
        assert len(bm) == 7  # 7 rows
        assert all(len(row) > 0 for row in bm)  # non-empty rows

    def test_bitmap_multi_char(self):
        bm = MicroFont.get_bitmap("AB")
        assert len(bm) == 7
        bm_a = MicroFont.get_bitmap("A")
        # Multi-char bitmap should be wider than single char
        assert len(bm[0]) > len(bm_a[0])

    def test_consistent_rendering(self):
        """Same input produces same output."""
        bm1 = MicroFont.get_bitmap("HELLO")
        bm2 = MicroFont.get_bitmap("HELLO")
        assert bm1 == bm2

    def test_live_icon_frame(self):
        frame = MicroFont.get_live_icon_frame(0.0)
        assert len(frame) == 7
        assert len(frame[0]) == 6
        # At t=0 (idle phase), all segments should be dim (1)
        for r in range(6):
            for c in range(6):
                val = frame[r][c]
                assert val in [0, 1]  # Either transparent or dim

    def test_live_icon_animation(self):
        # At t=3.2s (frame 2), some segments should be lit
        frame = MicroFont.get_live_icon_frame(3.2)
        has_lit = any(frame[r][c] == 2 for r in range(6) for c in range(6))
        assert has_lit


# ---------------------------------------------------------------------------
# Backward compat: LEDSimulator alias
# ---------------------------------------------------------------------------


def test_led_simulator_alias():
    from transit_tracker.simulator import LEDSimulator

    assert LEDSimulator is TUISimulator


# ---------------------------------------------------------------------------
# Module-level functions exist
# ---------------------------------------------------------------------------


def test_module_functions_importable():
    from transit_tracker.simulator import async_run_simulator, run_simulator

    assert callable(async_run_simulator)
    assert callable(run_simulator)


# ---------------------------------------------------------------------------
# TUISimulator instantiation
# ---------------------------------------------------------------------------


def test_tui_simulator_creates():
    config = TransitConfig()
    sim = TUISimulator(config, force_live=True)
    assert sim.running is True
    assert sim.state == {}
