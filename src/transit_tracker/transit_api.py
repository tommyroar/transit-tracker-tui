import httpx
import urllib.parse
from typing import List, Tuple, Dict, Any, Optional

class TransitAPIError(Exception):
    pass

class TransitAPI:
    def __init__(self):
        self.oba_base_url = "https://api.pugetsound.onebusaway.org/api/where"
        self.oba_key = "TEST"
        self.client = httpx.AsyncClient(timeout=10.0)

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

    async def close(self):
        await self.client.aclose()
