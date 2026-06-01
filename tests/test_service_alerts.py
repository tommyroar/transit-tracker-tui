"""Tests for OBA service-alert (situation) handling.

Covers the full pipeline: parsing OBA situations (transit_api), route-level
fetch, the server's aggregation/logging/state, InfluxDB mirroring, the web
endpoint, and the simulator's alert indicator.
"""

import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from transit_tracker.network import websocket_server
from transit_tracker.network.websocket_server import TransitServer
from transit_tracker.transit_api import TransitAPI, _parse_situation

pytestmark = pytest.mark.unit


# Raw OBA situation, mirroring the live MAINTENANCE alert (40_17984).
RAW_SITUATION = {
    "id": "40_17984",
    "severity": "noImpact",
    "reason": "MAINTENANCE",
    "summary": {"lang": "en", "value": "Shuttle buses replacing 2 Line trains."},
    "description": {"lang": "en", "value": "IDC to South Bellevue."},
    "activeWindows": [{"from": 1780135200000, "to": 1780306200000}],
    "allAffects": [
        {"agencyId": "40", "routeId": "40_2LINE", "stopId": ""},
        {"agencyId": "40", "routeId": "", "stopId": "40_E01-T1"},
    ],
    "url": {"value": "https://example.com/alert"},
}


# ---------------------------------------------------------------------------
# _parse_situation
# ---------------------------------------------------------------------------


class TestParseSituation:
    def test_flattens_fields(self):
        a = _parse_situation(RAW_SITUATION)
        assert a["id"] == "40_17984"
        assert a["reason"] == "MAINTENANCE"
        assert a["summary"] == "Shuttle buses replacing 2 Line trains."
        assert a["url"] == "https://example.com/alert"
        # epoch ms -> s
        assert a["active_from"] == 1780135200
        assert a["active_to"] == 1780306200
        # affects = sorted route + stop ids
        assert a["affects"] == ["40_2LINE", "40_E01-T1"]

    def test_tolerates_missing_fields(self):
        a = _parse_situation({"id": "x"})
        assert a["id"] == "x"
        assert a["summary"] == ""
        assert a["active_from"] is None and a["active_to"] is None
        assert a["affects"] == []


# ---------------------------------------------------------------------------
# TransitAPI.get_route_alerts
# ---------------------------------------------------------------------------


def _api_with_response(payload):
    api = TransitAPI("k")
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    api.client = AsyncMock()
    api.client.get = AsyncMock(return_value=resp)
    return api


class TestGetRouteAlerts:
    @pytest.mark.asyncio
    async def test_extracts_situations_from_references(self):
        api = _api_with_response(
            {"code": 200, "data": {"references": {"situations": [RAW_SITUATION]}}}
        )
        alerts = await api.get_route_alerts("st:40_2LINE")
        assert len(alerts) == 1 and alerts[0]["id"] == "40_17984"
        # route id cleaned of feed prefix in the request URL
        assert "40_2LINE" in api.client.get.call_args[0][0]

    @pytest.mark.asyncio
    async def test_null_body_returns_empty(self):
        api = _api_with_response(None)
        assert await api.get_route_alerts("st:40_2LINE") == []

    @pytest.mark.asyncio
    async def test_errors_swallowed(self):
        api = TransitAPI("k")
        api.client = AsyncMock()
        api.client.get = AsyncMock(side_effect=RuntimeError("boom"))
        assert await api.get_route_alerts("st:40_2LINE") == []  # never raises


# ---------------------------------------------------------------------------
# InfluxDBWriter.enqueue_alert
# ---------------------------------------------------------------------------


class TestEnqueueAlert:
    def test_writes_service_alert_line(self, monkeypatch):
        import urllib.request

        from transit_tracker.observability.influxdb_writer import InfluxDBWriter

        captured = {}

        class _Resp:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _fake(req, timeout=5):
            captured["body"] = req.data.decode()
            return _Resp()

        monkeypatch.setattr(urllib.request, "urlopen", _fake)
        w = InfluxDBWriter("http://x:8086", "tok", "home", "b", flush_interval_s=0.05)
        try:
            w.enqueue_alert(_parse_situation(RAW_SITUATION), ts_seconds=1780200000)
            deadline = time.monotonic() + 2
            while w.qsize() > 0 and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(0.1)
        finally:
            w.shutdown(timeout=2)
        body = captured.get("body", "")
        assert body.startswith("service_alert,")
        assert "reason=MAINTENANCE" in body
        assert "alert_id=40_17984" in body
        assert "active=true" in body


# ---------------------------------------------------------------------------
# TransitServer.refresh_alerts
# ---------------------------------------------------------------------------


@pytest.fixture
def server():
    config = MagicMock()
    config.service = MagicMock()
    config.service.oba_api_key = None
    config.service.check_interval_seconds = 30
    config.service.display_brightness = 128
    config.service.use_local_api = True
    config.transit_tracker = MagicMock()
    config.transit_tracker.time_display = "arrival"
    config.transit_tracker.abbreviations = []
    s = TransitServer(config)
    s.subscriptions = {
        MagicMock(): [{"routeId": "st:40_2LINE", "stopId": "st:40_E01-T1"}]
    }
    return s


@pytest.fixture
def server_records():
    captured = []

    class _Rec(logging.Handler):
        def emit(self, record):
            captured.append(record)

    h = _Rec()
    logger = websocket_server.log
    prev = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(h)
    try:
        yield captured
    finally:
        logger.removeHandler(h)
        logger.setLevel(prev)


def _future_alert():
    a = _parse_situation(RAW_SITUATION)
    a["active_from"] = None
    a["active_to"] = int(time.time()) + 3600  # active now
    return a


class TestRefreshAlerts:
    @pytest.mark.asyncio
    async def test_active_alert_collected_and_logged_once(self, server, server_records):
        server.api.get_route_alerts = AsyncMock(return_value=[_future_alert()])
        await server.refresh_alerts()
        assert "40_17984" in server.active_alerts
        await server.refresh_alerts()  # still active -> not re-logged
        warns = [r for r in server_records if r.levelno == logging.WARNING]
        assert len(warns) == 1
        assert "MAINTENANCE" in warns[0].getMessage()

    @pytest.mark.asyncio
    async def test_expired_window_excluded(self, server):
        past = _parse_situation(RAW_SITUATION)
        past["active_from"] = None
        past["active_to"] = int(time.time()) - 10
        server.api.get_route_alerts = AsyncMock(return_value=[past])
        await server.refresh_alerts()
        assert server.active_alerts == {}

    @pytest.mark.asyncio
    async def test_resolution_logged_and_cleared(self, server, server_records):
        server.api.get_route_alerts = AsyncMock(return_value=[_future_alert()])
        await server.refresh_alerts()
        server.api.get_route_alerts = AsyncMock(return_value=[])  # resolved
        await server.refresh_alerts()
        assert server.active_alerts == {}
        assert [r for r in server_records if "cleared" in r.getMessage()]


# ---------------------------------------------------------------------------
# Web endpoint
# ---------------------------------------------------------------------------


class TestHandleAlerts:
    def test_reads_alerts_from_state_file(self, tmp_path, monkeypatch):
        from transit_tracker.web import api_handlers

        state = tmp_path / "service_state.json"
        state.write_text(json.dumps({"alerts": [{"id": "40_17984"}]}))
        monkeypatch.setattr(websocket_server, "SERVICE_STATE_FILE", str(state))
        assert api_handlers._handle_alerts() == {"alerts": [{"id": "40_17984"}]}

    def test_missing_state_file_returns_empty(self, tmp_path, monkeypatch):
        from transit_tracker.web import api_handlers

        monkeypatch.setattr(
            websocket_server, "SERVICE_STATE_FILE", str(tmp_path / "nope.json")
        )
        assert api_handlers._handle_alerts() == {"alerts": []}


# ---------------------------------------------------------------------------
# Simulator alert indicator
# ---------------------------------------------------------------------------


class TestSimulatorAlertIcon:
    def test_icon_frame_shape_and_blink(self):
        from transit_tracker.simulator import MicroFont

        on = MicroFont.get_alert_icon_frame(0.0)  # within lit window
        off = MicroFont.get_alert_icon_frame(0.8)  # within dark window
        assert len(on) == 7 and all(len(r) == 6 for r in on)
        # the exclamation stem pixel is lit (2) when on, dim (1) when off
        assert on[0][2] == 2 and off[0][2] == 1
        # transparent cells stay 0 in both
        assert on[0][0] == 0 and off[0][0] == 0

    def _sim(self, demo_alert=False, alerts=None):
        from transit_tracker.simulator import TUISimulator

        sim = TUISimulator.__new__(TUISimulator)  # skip __init__/WS setup
        sim.demo_alert = demo_alert
        sim.state = {"live": {"alerts": alerts or []}}
        return sim

    def test_get_active_alerts_filters_expired(self):
        sim = self._sim(alerts=[
            {"id": "live", "active_to": int(time.time()) + 99},
            {"id": "old", "active_to": int(time.time()) - 99},
            {"id": "forever", "active_to": None},
        ])
        ids = {a["id"] for a in sim.get_active_alerts()}
        assert ids == {"live", "forever"}

    def test_demo_alert_injects_synthetic_alert(self):
        sim = self._sim(demo_alert=True)  # no live alerts
        alerts = sim.get_active_alerts()
        assert [a["id"] for a in alerts] == ["demo"]
        assert alerts[0]["reason"] == "MAINTENANCE"

    def test_demo_alert_off_injects_nothing(self):
        assert self._sim(demo_alert=False).get_active_alerts() == []

    def test_demo_alert_not_duplicated(self):
        # A real "demo"-id alert from the wire shouldn't be doubled.
        sim = self._sim(demo_alert=True, alerts=[{"id": "demo", "active_to": None}])
        assert [a["id"] for a in sim.get_active_alerts()] == ["demo"]
