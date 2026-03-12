import asyncio
import json
import os
import time
import websockets
from typing import Dict, Set, List, Any, Tuple
from ..transit_api import TransitAPI
from ..config import TransitConfig

SERVICE_STATE_FILE = os.path.join(os.path.expanduser("~/.config/transit-tracker"), "service_state.json")

def update_service_state(data: Dict[str, Any]):
    try:
        os.makedirs(os.path.dirname(SERVICE_STATE_FILE), exist_ok=True)
        # Use a temporary file and rename for atomicity
        temp_file = SERVICE_STATE_FILE + ".tmp"
        with open(temp_file, "w") as f:
            json.dump(data, f)
        os.rename(temp_file, SERVICE_STATE_FILE)
    except Exception as e:
        print(f"[SERVER] Error updating state file: {e}")

class TransitServer:
    def __init__(self, config: TransitConfig):
        self.config = config
        self.api = TransitAPI()
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.subscriptions: Dict[websockets.WebSocketServerProtocol, List[Dict[str, str]]] = {}
        self.client_names: Dict[websockets.WebSocketServerProtocol, str] = {}
        self.cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self.in_flight: Dict[str, asyncio.Task] = {}
        self.cache_ttl = 30 # seconds
        self.last_broadcast_time = 0
        self.start_time = time.time()
        self.messages_processed = 0

    def sync_state(self, last_message: Dict[str, Any] = None):
        client_list = []
        for c in self.clients:
            addr = getattr(c, "remote_address", None)
            if addr:
                name = self.client_names.get(c, "Unknown Device")
                client_list.append({
                    "address": f"{addr[0]}:{addr[1]}",
                    "name": name,
                    "subscriptions": len(self.subscriptions.get(c, []))
                })
        
        state = {
            "last_update": self.last_broadcast_time,
            "heartbeat": time.time(),
            "start_time": self.start_time,
            "messages_processed": self.messages_processed,
            "pid": os.getpid(),
            "status": "active",
            "clients": client_list,
            "client_count": len(self.clients)
        }
        if last_message:
            state["last_message"] = last_message
        else:
            # Try to preserve existing last_message if not provided
            try:
                if os.path.exists(SERVICE_STATE_FILE):
                    with open(SERVICE_STATE_FILE, "r") as f:
                        old_state = json.load(f)
                        if "last_message" in old_state:
                            state["last_message"] = old_state["last_message"]
            except Exception:
                pass
                
        update_service_state(state)

    async def get_arrivals_cached(self, stop_id: str) -> List[Dict[str, Any]]:
        now = asyncio.get_event_loop().time()
        print(f"[SERVER] Fetching arrivals for {stop_id}...")
        if stop_id in self.cache:
            ts, data = self.cache[stop_id]
            if now - ts < self.cache_ttl:
                print(f"[SERVER] Cache hit for {stop_id}, returning {len(data)} trips")
                return data
                
        # If there's already a request in flight for this stop, wait for it
        if stop_id in self.in_flight:
            print(f"[SERVER] Request in flight for {stop_id}, waiting...")
            return await self.in_flight[stop_id]
            
        async def fetch():
            # Strip prefix just in case
            clean_stop_id = stop_id
            if ":" in stop_id and "_" in stop_id:
                colon_idx = stop_id.find(":")
                underscore_idx = stop_id.find("_")
                if colon_idx < underscore_idx:
                    clean_stop_id = stop_id[colon_idx+1:]

            print(f"[SERVER] Making OBA API call for clean_stop_id={clean_stop_id}...")
            arrivals = await self.api.get_arrivals(clean_stop_id)
            print(f"[SERVER] Received {len(arrivals)} arrivals for {clean_stop_id}")
            self.cache[stop_id] = (asyncio.get_event_loop().time(), arrivals)
            return arrivals

        task = asyncio.create_task(fetch())
        self.in_flight[stop_id] = task
        try:
            return await task
        finally:
            if stop_id in self.in_flight:
                del self.in_flight[stop_id]

    async def register(self, ws: websockets.WebSocketServerProtocol):
        self.clients.add(ws)
        self.sync_state()
        print(f"[SERVER] Client connected: {ws.remote_address}")
        try:
            async for message in ws:
                data = json.loads(message)
                # Support both 'event' and 'type' keys
                event = data.get("event") or data.get("type")
                if event == "schedule:subscribe":
                    # Support both 'data' and 'payload'
                    payload = data.get("data") or data.get("payload") or {}
                    
                    # Store client name if provided
                    if "client_name" in payload:
                        self.client_names[ws] = payload["client_name"]
                    elif "client_name" in data:
                        self.client_names[ws] = data["client_name"]
                    
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
                        self.sync_state()
                        print(f"[SERVER] Client {ws.remote_address} subscribed to {len(pairs)} pairs")
                        # Send immediate update
                        await self.send_update(ws)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.clients.remove(ws)
            if ws in self.subscriptions:
                del self.subscriptions[ws]
            if ws in self.client_names:
                del self.client_names[ws]
            self.sync_state()
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

        # Always send an update, even if empty, so the client knows we checked
        # Send in both formats for compatibility
        response = {
            "event": "schedule",
            "type": "schedule",
            "data": {"trips": all_trips},
            "payload": {"trips": all_trips, "stopId": subs[0]["stopId"]} # stopId for compat
        }
        await ws.send(json.dumps(response))
        self.messages_processed += 1
        self.last_broadcast_time = time.time()
        self.sync_state(last_message=response)

    async def broadcast_loop(self):
        while True:
            # Copy clients to avoid mutation during iteration
            for ws in list(self.clients):
                if ws in self.subscriptions:
                    await self.send_update(ws)
            
            # Always update heartbeat so GUI knows we are alive
            self.sync_state()
            await asyncio.sleep(self.config.check_interval_seconds)

async def run_server(host: str = "0.0.0.0", port: int = 8000, config: TransitConfig = None):
    if config is None:
        config = TransitConfig.load()
    server = TransitServer(config)
    print(f"[SERVER] Starting Transit Tracker API on {host}:{port}")
    
    async with websockets.serve(server.register, host, port):
        await server.broadcast_loop()
