"""Unit tests for the display module.


Tests template variable building, trip line formatting, and bitmap
segment generation — all pure functions with no external dependencies.
"""

import pytest

from transit_tracker.display import (
    DEFAULT_DISPLAY_FORMAT,
    DISPLAY_VARIABLES,
    build_bitmap_segments,
    build_trip_variables,
    format_trip_line,
)

pytestmark = pytest.mark.unit
NOW = 1_700_000_000.0


def _trip(**overrides):
    base = {
        "routeName": "554",
        "headsign": "Downtown Seattle",
        "arrivalTime": 1_700_000_600,
        "isRealtime": True,
        "routeId": "st:40_100240",
        "stopId": "st:1_8494",
        "routeColor": "2B376E",
    }
    base.update(overrides)
    return base


# -- build_trip_variables ----------------------------------------------------


class TestBuildTripVariables:
    def test_all_keys_present(self):
        variables = build_trip_variables(_trip(), NOW)
        for key in DISPLAY_VARIABLES:
            assert key in variables, f"Missing variable: {key}"

    def test_millisecond_arrival(self):
        trip = _trip(arrivalTime=1_700_000_600_000)
        variables = build_trip_variables(trip, NOW)
        assert variables["TIME"] == "10m"

    def test_zero_arrival(self):
        trip = _trip(arrivalTime=0)
        variables = build_trip_variables(trip, NOW)
        assert variables["WAIT"] == "-1"
        assert variables["TIME"] == "Now"

    def test_now_threshold(self):
        trip = _trip(arrivalTime=int(NOW) - 10)
        variables = build_trip_variables(trip, NOW)
        assert variables["TIME"] == "Now"

    def test_realtime_indicator_true(self):
        variables = build_trip_variables(_trip(isRealtime=True), NOW)
        assert variables["LIVE"] == "\u25c9"

    def test_realtime_indicator_false(self):
        variables = build_trip_variables(_trip(isRealtime=False), NOW)
        assert variables["LIVE"] == "\u25cb"

    def test_route_color_empty_when_none(self):
        variables = build_trip_variables(_trip(routeColor=None), NOW)
        assert variables["ROUTE_COLOR"] == ""


# -- format_trip_line --------------------------------------------------------


class TestFormatTripLine:
    def test_default_format(self):
        line = format_trip_line(_trip(), NOW)
        assert line == "554  Downtown Seattle  \u25c9 10m"

    def test_custom_format(self):
        line = format_trip_line(_trip(), NOW, fmt="{ROUTE} \u2192 {HEADSIGN} {TIME}")
        assert line == "554 \u2192 Downtown Seattle 10m"

    def test_invalid_template_fallback(self):
        line = format_trip_line(_trip(), NOW, fmt="{NONEXISTENT}")
        assert "554" in line
        assert "Downtown Seattle" in line

    def test_none_format_uses_default(self):
        line = format_trip_line(_trip(), NOW, fmt=None)
        assert line == format_trip_line(_trip(), NOW)

    def test_live_only_template(self):
        trip = _trip(isRealtime=False, arrivalTime=int(NOW) + 300)
        line = format_trip_line(trip, NOW, fmt="{LIVE} {WAIT}min")
        assert line == "\u25cb 5min"

    def test_default_format_matches_constant(self):
        line_default = format_trip_line(_trip(), NOW)
        line_explicit = format_trip_line(_trip(), NOW, fmt=DEFAULT_DISPLAY_FORMAT)
        assert line_default == line_explicit


# -- build_bitmap_segments ---------------------------------------------------


class TestBuildBitmapSegments:
    def _dep(self, **overrides):
        base = {
            "route": "554",
            "headsign": "Downtown Seattle",
            "diff": 10,
            "live": True,
            "color": "yellow",
            "stop_id": "1_8494",
        }
        base.update(overrides)
        return base

    def test_default_segment_count(self):
        segs = build_bitmap_segments(self._dep())
        # Default: "{ROUTE}  {HEADSIGN}  {LIVE} {TIME}"
        # 4 variables + 3 literals = 7 segments
        assert len(segs) >= 4

    def test_segment_structure(self):
        segs = build_bitmap_segments(self._dep())
        for seg in segs:
            assert "variable" in seg
            assert "text" in seg
            assert "role" in seg

    def test_literal_text_between_vars(self):
        segs = build_bitmap_segments(self._dep())
        literals = [s for s in segs if s["variable"] is None]
        assert len(literals) > 0
        assert any("  " in s["text"] for s in literals)

    def test_color_roles(self):
        dep = self._dep(color="green")
        segs = build_bitmap_segments(dep)
        route_seg = next(s for s in segs if s["variable"] == "ROUTE")
        assert route_seg["role"] == "green"
        live_seg = next(s for s in segs if s["variable"] == "LIVE")
        assert live_seg["role"] == "live_icon"

    def test_time_role_realtime(self):
        segs = build_bitmap_segments(self._dep(live=True))
        time_seg = next(s for s in segs if s["variable"] == "TIME")
        assert time_seg["role"] == "bright_blue"

    def test_time_role_scheduled(self):
        segs = build_bitmap_segments(self._dep(live=False))
        time_seg = next(s for s in segs if s["variable"] == "TIME")
        assert time_seg["role"] == "grey74"

    def test_custom_format(self):
        segs = build_bitmap_segments(self._dep(), fmt="{ROUTE} {TIME}")
        vars_found = [s["variable"] for s in segs if s["variable"]]
        assert "ROUTE" in vars_found
        assert "TIME" in vars_found
