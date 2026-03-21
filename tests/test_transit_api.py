"""Unit tests for the transit_api module.


Tests stop ID cleaning (static method), geocoding, and arrival
deduplication with mocked HTTP responses.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from transit_tracker.transit_api import TransitAPI, TransitAPIError

pytestmark = pytest.mark.unit

# -- _clean_stop_id (static, no mocking needed) -----------------------------


class TestCleanStopId:
    def test_passthrough_plain_id(self):
        assert TransitAPI._clean_stop_id("1_8494") == "1_8494"

    def test_wsf_prefix(self):
        assert TransitAPI._clean_stop_id("wsf:7") == "95_7"

    def test_wsf_prefix_multi_digit(self):
        assert TransitAPI._clean_stop_id("wsf:12") == "95_12"

    def test_st_prefix(self):
        assert TransitAPI._clean_stop_id("st:1_8494") == "1_8494"

    def test_no_underscore_after_colon(self):
        # "foo:bar" has colon but no underscore after it
        assert TransitAPI._clean_stop_id("foo:bar") == "foo:bar"

    def test_underscore_before_colon(self):
        # Underscore before colon — should not strip
        assert TransitAPI._clean_stop_id("1_foo:bar") == "1_foo:bar"

    def test_empty_string(self):
        assert TransitAPI._clean_stop_id("") == ""

    def test_numeric_only(self):
        assert TransitAPI._clean_stop_id("12345") == "12345"


# -- geocode -----------------------------------------------------------------


class TestGeocode:
    @pytest.mark.asyncio
    async def test_success(self):
        api = TransitAPI(oba_api_key="TEST")
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"lat": "47.6062", "lon": "-122.3321", "display_name": "Seattle, WA"}
        ]
        mock_response.raise_for_status = MagicMock()
        api.client = AsyncMock()
        api.client.get = AsyncMock(return_value=mock_response)

        result = await api.geocode("Seattle")
        assert result is not None
        lat, lon, name = result
        assert abs(lat - 47.6062) < 0.001
        assert abs(lon - (-122.3321)) < 0.001
        assert name == "Seattle, WA"

    @pytest.mark.asyncio
    async def test_empty_result(self):
        api = TransitAPI(oba_api_key="TEST")
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        api.client = AsyncMock()
        api.client.get = AsyncMock(return_value=mock_response)

        result = await api.geocode("nonexistent place")
        assert result is None

    @pytest.mark.asyncio
    async def test_http_error(self):
        api = TransitAPI(oba_api_key="TEST")
        api.client = AsyncMock()
        api.client.get = AsyncMock(side_effect=Exception("connection refused"))

        with pytest.raises(TransitAPIError, match="Geocoding failed"):
            await api.geocode("Seattle")


# -- get_arrivals ------------------------------------------------------------


class TestGetArrivals:
    def _make_oba_response(self, arrivals, routes=None):
        """Build a mock OBA arrivals-and-departures response."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "code": 200,
            "data": {
                "entry": {"arrivalsAndDepartures": arrivals},
                "references": {"routes": routes or []},
            },
        }
        return mock_response

    @pytest.mark.asyncio
    async def test_basic_arrival(self):
        api = TransitAPI(oba_api_key="TEST")
        arrivals = [
            {
                "tripId": "t1",
                "routeId": "40_100240",
                "stopId": "1_8494",
                "predictedArrivalTime": 1700000600000,
                "scheduledArrivalTime": 1700000600000,
                "predictedDepartureTime": 1700000630000,
                "scheduledDepartureTime": 1700000630000,
                "routeShortName": "554",
                "tripHeadsign": "Downtown",
                "vehicleId": "v1",
            }
        ]
        api.client = AsyncMock()
        api.client.get = AsyncMock(return_value=self._make_oba_response(arrivals))

        result = await api.get_arrivals("1_8494")
        assert len(result) == 1
        assert result[0]["tripId"] == "t1"
        assert result[0]["isRealtime"] is True
        assert result[0]["routeName"] == "554"

    @pytest.mark.asyncio
    async def test_deduplication(self):
        api = TransitAPI(oba_api_key="TEST")
        arrivals = [
            {
                "tripId": "t1",
                "routeId": "40_100240",
                "predictedArrivalTime": 1700000600000,
                "scheduledArrivalTime": 1700000600000,
                "predictedDepartureTime": None,
                "scheduledDepartureTime": None,
                "tripHeadsign": "Downtown",
                "vehicleId": None,
            },
            {
                "tripId": "t1",
                "routeId": "40_100240",
                "predictedArrivalTime": 1700000600000,
                "scheduledArrivalTime": 1700000600000,
                "predictedDepartureTime": None,
                "scheduledDepartureTime": None,
                "tripHeadsign": "Downtown",
                "vehicleId": "v1",
            },
        ]
        api.client = AsyncMock()
        api.client.get = AsyncMock(return_value=self._make_oba_response(arrivals))

        result = await api.get_arrivals("1_8494")
        assert len(result) == 1
        # Should keep the one with vehicleId
        assert result[0]["vehicleId"] == "v1"

    @pytest.mark.asyncio
    async def test_http_error(self):
        api = TransitAPI(oba_api_key="TEST")
        api.client = AsyncMock()
        api.client.get = AsyncMock(side_effect=Exception("timeout"))

        with pytest.raises(TransitAPIError, match="Failed to fetch arrivals"):
            await api.get_arrivals("1_8494")

    @pytest.mark.asyncio
    async def test_predicted_zero_is_scheduled(self):
        """predictedArrivalTime=0 should be treated as scheduled (not realtime)."""
        api = TransitAPI(oba_api_key="TEST")
        arrivals = [
            {
                "tripId": "t1",
                "routeId": "40_100240",
                "predictedArrivalTime": 0,
                "scheduledArrivalTime": 1700000600000,
                "predictedDepartureTime": 0,
                "scheduledDepartureTime": 1700000630000,
                "tripHeadsign": "Downtown",
            },
        ]
        api.client = AsyncMock()
        api.client.get = AsyncMock(return_value=self._make_oba_response(arrivals))

        result = await api.get_arrivals("1_8494")
        assert len(result) == 1
        assert result[0]["isRealtime"] is False
        assert result[0]["arrivalTime"] == 1700000600000
