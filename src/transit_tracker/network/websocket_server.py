import asyncio
import json
import websockets
from typing import Dict, Set, List, Any, Tuple
from ..transit_api import TransitAPI
from ..config import TransitConfig

class TransitServer:
    def __init__(self, config: TransitConfig):
        self.config = config
        self.api = TransitAPI()
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.subscriptions: Dict[websockets.WebSocketServerProtocol, List[Dict[str, str]]] = {}
        self.cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self.cache_ttl = 30 # seconds

    async def get_arrivals_cached(self, stop_id: str) -> List[Dict[str, Any]]:
        now = asyncio.get_event_loop().time()
        if stop_id in self.cache:
            ts, data = self.cache[stop_id]
            if now - ts < self.cache_ttl:
                return data
        
        # Strip prefix just in case
        clean_stop_id = stop_id
        if ":" in stop_id and "_" in stop_id:
            colon_idx = stop_id.find(":")
            underscore_idx = stop_id.find("_")
            if colon_idx < underscore_idx:
                clean_stop_id = stop_id[colon_idx+1:]

        arrivals = await self.api.get_arrivals(clean_stop_id)
        self.cache[stop_id] = (now, arrivals)
        return arrivals

    async def register(self, ws: websockets.WebSocketServerProtocol):
        self.clients.add(ws)
        print(f"[SERVER] Client connected: {ws.remote_address}")
        try:
            async for message in ws:
                data = json.loads(message)
                # Support both 'event' and 'type' keys
                event = data.get("event") or data.get("type")
                if event == "schedule:subscribe":
                    # Support both 'data' and 'payload'
                    payload = data.get("data") or data.get("payload") or {}
                    
                    pairs = []
                    # Case 1: routeStopPairs string (TJ Horner style)
                    pairs_str = payload.get("routeStopPairs")
                    if pairs_str:
                        for pair in pairs_str.split(";"):
                            if "," in pair:
                                r_id, s_id = pair.split(",")
                                pairs.append({"routeId": r_id, "stopId": s_id})
                    # Case 2: Individual stop/route (Older/Custom style)
                    elif "stopId" in payload:
                        pairs.append({
                            "routeId": payload.get("routeId"), 
                            "stopId": payload.get("stopId")
                        })
                    
                    if pairs:
                        self.subscriptions[ws] = pairs
                        print(f"[SERVER] Client {ws.remote_address} subscribed to {len(pairs)} pairs")
                        # Send immediate update
                        await self.send_update(ws)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.remove(ws)
            if ws in self.subscriptions:
                del self.subscriptions[ws]
            print(f"[SERVER] Client disconnected: {ws.remote_address}")

    async def send_update(self, ws: websockets.WebSocketServerProtocol):
        subs = self.subscriptions.get(ws, [])
        if not subs:
            return

        all_trips = []
        # Group by stop to avoid redundant API calls
        stops = set(s["stopId"] for s in subs)
        
        for stop_id in stops:
            try:
                # OBA expects clean stop ID
                clean_stop_id = stop_id
                if ":" in stop_id and "_" in stop_id:
                    colon_idx = stop_id.find(":")
                    underscore_idx = stop_id.find("_")
                    if colon_idx < underscore_idx:
                        clean_stop_id = stop_id[colon_idx+1:]
                
                arrivals = await self.get_arrivals_cached(clean_stop_id)
                
                # Normalize relevant route IDs for comparison
                def normalize_route(r_id):
                    if ":" in r_id and "_" in r_id:
                        c_idx = r_id.find(":")
                        u_idx = r_id.find("_")
                        if c_idx < u_idx:
                            return r_id[c_idx+1:]
                    return r_id

                relevant_routes = set(normalize_route(s["routeId"]) for s in subs if s["stopId"] == stop_id)
                
                for arr in arrivals:
                    arr_route_id = normalize_route(arr["routeId"])
                    if not relevant_routes or arr_route_id in relevant_routes:
                        # Ensure the response has the original stopId from sub
                        arr_copy = arr.copy()
                        arr_copy["stopId"] = stop_id
                        all_trips.append(arr_copy)
            except Exception as e:
                print(f"[SERVER] Error fetching arrivals for {stop_id}: {e}")

        if all_trips:
            # Send in both formats for compatibility
            response = {
                "event": "schedule",
                "type": "schedule",
                "data": {"trips": all_trips},
                "payload": {"trips": all_trips, "stopId": subs[0]["stopId"]} # stopId for compat
            }
            await ws.send(json.dumps(response))

    async def broadcast_loop(self):
        while True:
            # Copy clients to avoid mutation during iteration
            for ws in list(self.clients):
                if ws in self.subscriptions:
                    await self.send_update(ws)
            await asyncio.sleep(self.config.check_interval_seconds)

async def run_server(host: str = "0.0.0.0", port: int = 8000, config: TransitConfig = None):
    if config is None:
        config = TransitConfig.load()
    server = TransitServer(config)
    print(f"[SERVER] Starting Transit Tracker API on {host}:{port}")
    
    async with websockets.serve(server.register, host, port):
        await server.broadcast_loop()
