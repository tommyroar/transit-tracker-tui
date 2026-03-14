import urllib.parse
from typing import Any, Dict, List, Optional, Tuple

import httpx


class TransitAPIError(Exception):
    pass

class TransitAPI:
    def __init__(self):
        self.oba_base_url = "https://api.pugetsound.onebusaway.org/api/where"
        self.oba_key = "TEST"
        self.client = httpx.AsyncClient(timeout=10.0)

    @staticmethod
    def _clean_stop_id(stop_id: str) -> str:
        """Strip internal feed prefix if present (e.g. 'st:1_8494' -> '1_8494')."""
        if ":" in stop_id and "_" in stop_id:
            colon_idx = stop_id.find(":")
            underscore_idx = stop_id.find("_")
            if colon_idx < underscore_idx:
                return stop_id[colon_idx + 1:]
        return stop_id

    async def geocode(self, query: str) -> Optional[Tuple[float, float, str]]:
        """
        Geocodes a street intersection or address using Nominatim.
        """
        url = "https://nominatim.openstreetmap.org/search"
        params = {
            "q": query,
            "format": "json",
            "limit": "1"
        }
        headers = {"User-Agent": "TransitTracker/1.0"}
        
        try:
            response = await self.client.get(url, params=params, headers=headers)
            response.raise_for_status()
            data = response.json()
            if data:
                return float(data[0]["lat"]), float(data[0]["lon"]), data[0]["display_name"]
            return None
        except Exception as e:
            raise TransitAPIError(f"Geocoding failed: {e}")

    async def get_routes_for_location(self, lat: float, lon: float, radius: int = 1500) -> List[Dict[str, Any]]:
        """
        Fetches transit routes within a radius of a location.
        """
        url = f"{self.oba_base_url}/routes-for-location.json"
        params = {
            "key": self.oba_key,
            "lat": lat,
            "lon": lon,
            "radius": radius
        }
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                return data["data"]["list"]
            return []
        except Exception as e:
            raise TransitAPIError(f"Failed to fetch routes: {e}")

    async def get_stops_for_route(self, route_id: str) -> List[Dict[str, Any]]:
        """
        Fetches all stops for a specific route.
        """
        url = f"{self.oba_base_url}/stops-for-route/{urllib.parse.quote(route_id)}.json"
        params = {"key": self.oba_key}
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                # OBA returns stops in 'references.stops' and stop groupings in 'entry.stopGroupings'
                # We extract stops and directions.
                stops_data = {s["id"]: s for s in data["data"]["references"]["stops"]}
                groupings = data["data"]["entry"]["stopGroupings"]
                
                results = []
                for grouping in groupings:
                    for stop_group in grouping["stopGroups"]:
                        direction_name = stop_group["name"]["name"]
                        for stop_id in stop_group["stopIds"]:
                            stop = stops_data.get(stop_id)
                            if stop:
                                results.append({
                                    "id": stop["id"],
                                    "name": stop["name"],
                                    "direction": stop.get("direction"),
                                    "direction_name": direction_name,
                                    "lat": stop["lat"],
                                    "lon": stop["lon"]
                                })
                return results
            return []
        except Exception as e:
            raise TransitAPIError(f"Failed to fetch stops: {e}")

    @staticmethod
    def _decode_polyline(encoded: str) -> List[List[float]]:
        """Decode a Google encoded polyline string into a list of [lng, lat] pairs."""
        coords = []
        index = 0
        lat = 0
        lng = 0
        while index < len(encoded):
            for is_lng in (False, True):
                shift = 0
                result = 0
                while True:
                    b = ord(encoded[index]) - 63
                    index += 1
                    result |= (b & 0x1F) << shift
                    shift += 5
                    if b < 0x20:
                        break
                delta = ~(result >> 1) if (result & 1) else (result >> 1)
                if is_lng:
                    lng += delta
                else:
                    lat += delta
            coords.append([lng / 1e5, lat / 1e5])
        return coords

    async def get_route_polylines(self, route_id: str) -> Dict[str, Any]:
        """Fetch route shape polylines and route metadata (color, name)."""
        clean_id = self._clean_stop_id(route_id)
        url = f"{self.oba_base_url}/stops-for-route/{urllib.parse.quote(clean_id)}.json"
        params = {"key": self.oba_key}

        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                polylines = data["data"]["entry"].get("polylines", [])
                coords_list = []
                for pl in polylines:
                    points = pl.get("points", "")
                    if points:
                        coords_list.append(self._decode_polyline(points))

                # Get route info from references
                routes_ref = {r["id"]: r for r in data["data"]["references"].get("routes", [])}
                route_info = routes_ref.get(clean_id, {})

                return {
                    "route_id": route_id,
                    "name": route_info.get("shortName", ""),
                    "color": route_info.get("color", ""),
                    "polylines": coords_list,
                }
            return {"route_id": route_id, "name": "", "color": "", "polylines": []}
        except Exception as e:
            raise TransitAPIError(f"Failed to fetch polylines for {route_id}: {e}")

    async def get_stop(self, stop_id: str) -> Optional[Dict[str, Any]]:
        """Fetches details for a single stop by ID, including lat/lon."""
        clean_stop_id = self._clean_stop_id(stop_id)
        url = f"{self.oba_base_url}/stop/{urllib.parse.quote(clean_stop_id)}.json"
        params = {"key": self.oba_key}

        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                s = data["data"]["entry"]
                return {
                    "id": stop_id,
                    "name": s["name"],
                    "lat": s["lat"],
                    "lon": s["lon"],
                }
            return None
        except Exception as e:
            raise TransitAPIError(f"Failed to fetch stop {stop_id}: {e}")

    async def get_arrivals(self, stop_id: str) -> List[Dict[str, Any]]:
        """
        Fetches real-time arrivals for a specific stop.
        """
        clean_stop_id = self._clean_stop_id(stop_id)
        url = f"{self.oba_base_url}/arrivals-and-departures-for-stop/{urllib.parse.quote(clean_stop_id)}.json"
        params = {"key": self.oba_key}
        
        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                arrivals = data["data"]["entry"]["arrivalsAndDepartures"]
                # Include references for route names/colors if needed
                routes = {r["id"]: r for r in data["data"]["references"].get("routes", [])}
                
                results = []
                for arr in arrivals:
                    route_id = arr["routeId"]
                    route_info = routes.get(route_id, {})
                    
                    results.append({
                        "tripId": arr["tripId"],
                        "routeId": route_id,
                        "stopId": stop_id,
                        "arrivalTime": arr.get("predictedArrivalTime") or arr.get("scheduledArrivalTime"),
                        "predictedArrivalTime": arr.get("predictedArrivalTime"),
                        "scheduledArrivalTime": arr.get("scheduledArrivalTime"),
                        "routeName": route_info.get("shortName") or arr.get("routeShortName"),
                        "headsign": arr.get("tripHeadsign"),
                        "isRealtime": arr.get("predictedArrivalTime") is not None,
                        "routeColor": route_info.get("color")
                    })
                return results
            return []
        except Exception as e:
            raise TransitAPIError(f"Failed to fetch arrivals: {e}")

    async def close(self):
        await self.client.aclose()
