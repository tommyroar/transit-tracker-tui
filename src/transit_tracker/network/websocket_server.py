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
        self.client_limits: Dict[websockets.WebSocketServerProtocol, int] = {}
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
                print(f"[SERVER] Received from {ws.remote_address}: {message}")
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
                    else:
                        # RELIABLE DETECTION: If an unknown device connects from a 
                        # known local IP or with specific subscription patterns, 
                        # label it as the Hardware Controller.
                        addr = getattr(ws, "remote_address", None)
                        if addr and (addr[0].startswith("192.168.") or addr[0].startswith("10.0.")):
                             self.client_names[ws] = "Hardware Controller"
                    
                    pairs = []
                    # Case 1: routeStopPairs string (TJ Horner style)
                    pairs_str = payload.get("routeStopPairs")
                    if pairs_str:
                        for pair in pairs_str.split(";"):
                            if "," in pair:
                                r_id, s_id = pair.split(",")
                                pairs.append({"routeId": r_id, "stopId": s_id})
                    elif pairs_str == "":
                        # FALLBACK: If device sends empty string, it might have lost its config.
                        # Use the server's own configured subscriptions as a default.
                        print(f"[SERVER] Client sent empty routeStopPairs, using server defaults.")
                        for sub in self.config.subscriptions:
                            pairs.append({"routeId": sub.route, "stopId": sub.stop})
                    # Case 2: Individual stop/route (Older/Custom style)
                    elif "stopId" in payload:
                        pairs.append({
                            "routeId": payload.get("routeId"), 
                            "stopId": payload.get("stopId")
                        })
                    
                    if pairs:
                        self.subscriptions[ws] = pairs
                        # Store limit if provided
                        limit = payload.get("limit")
                        if limit:
                            self.client_limits[ws] = int(limit)
                        
                        self.sync_state()
                        print(f"[SERVER] Client {ws.remote_address} subscribed to {len(pairs)} pairs (limit={limit})")
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
            if ws in self.client_limits:
                del self.client_limits[ws]
            self.sync_state()
            print(f"[SERVER] Client disconnected: {ws.remote_address}")

    async def send_update(self, ws: websockets.WebSocketServerProtocol):
        subs = self.subscriptions.get(ws, [])
        if not subs:
            return

        all_trips = []
        
        # Group by stop to avoid redundant API calls
        from collections import defaultdict
        stop_to_subs = defaultdict(list)
        for s in subs:
            stop_to_subs[s["stopId"]].append(s)

        def normalize_id(item_id):
            if item_id is None: return ""
            if ":" in item_id and "_" in item_id:
                c_idx = item_id.find(":")
                u_idx = item_id.find("_")
                if c_idx < u_idx:
                    return item_id[c_idx+1:]
            return item_id

        for stop_id, stop_subs in stop_to_subs.items():
            try:
                # OBA expects clean stop ID
                clean_stop_id = normalize_id(stop_id)
                arrivals = await self.get_arrivals_cached(clean_stop_id)
                
                # Map normalized routeId to its specific subscription for offset processing
                route_to_sub = {normalize_id(s.get("routeId")): s for s in stop_subs}
                relevant_routes = set(route_to_sub.keys())
                
                for arr in arrivals:
                    arr_route_id = normalize_id(arr["routeId"])
                    # If relevant_routes is effectively empty (e.g. subscribing to all routes at a stop)
                    # or if the specific route is matched, include it.
                    if not relevant_routes or "" in relevant_routes or None in relevant_routes or arr_route_id in relevant_routes:
                        arr_copy = arr.copy()
                        arr_copy["stopId"] = stop_id
                        
                        # Apply Travel Time Offset on the Server
                        # We must look this up in the server's master config because the client
                        # doesn't send offset info in the routeStopPairs protocol.
                        offset_sec = 0
                        master_sub = None
                        for s in self.config.subscriptions:
                            if normalize_id(s.route) == arr_route_id and normalize_id(s.stop) == clean_stop_id:
                                master_sub = s
                                break
                            
                        if master_sub and master_sub.time_offset:
                            try:
                                import re
                                match = re.search(r"(-?\d+)", str(master_sub.time_offset))
                                if match:
                                    offset_sec = int(match.group(1)) * 60
                            except (ValueError, TypeError):
                                pass

                        # Convert ms to seconds AND subtract offset for hardware compatibility
                        for key in ["arrivalTime", "predictedArrivalTime", "scheduledArrivalTime"]:
                            val = arr_copy.get(key)
                            if isinstance(val, (int, float)):
                                if val > 10**12: # Milliseconds
                                    val = int(val // 1000)
                                arr_copy[key] = val + offset_sec
                            elif val and isinstance(val, str) and val.isdigit():
                                # Handle stringified numbers if they occur
                                ival = int(val)
                                if ival > 10**12:
                                    ival = ival // 1000
                                arr_copy[key] = ival + offset_sec
                                
                        all_trips.append(arr_copy)
            except Exception as e:
                print(f"[SERVER] Error fetching arrivals for {stop_id}: {e}")

        # 1. Sort all aggregated trips by arrival time
        all_trips.sort(key=lambda x: x.get("arrivalTime", 0))

        # 2. Apply "Fair" Diversity Capping to respect hardware vertical space
        # We want to ensure at least one trip from each stop is shown if possible,
        # but stay strictly within the hardware's requested limit.
        limit = self.client_limits.get(ws, 3)
        
        final_trips = []
        seen_stops = set()
        
        # Pass 1: Get the soonest arrival for every stop
        for trip in all_trips:
            stop_id = trip.get("stopId")
            if stop_id not in seen_stops:
                final_trips.append(trip)
                seen_stops.add(stop_id)
            if len(final_trips) >= limit:
                break
                
        # Pass 2: Fill remaining slots with the next soonest arrivals overall
        if len(final_trips) < limit:
            for trip in all_trips:
                if trip not in final_trips:
                    final_trips.append(trip)
                if len(final_trips) >= limit:
                    break
        
        # Sort the final subset by time so they appear in order on the board
        final_trips.sort(key=lambda x: x.get("arrivalTime", 0))

        # 3. Build the response
        response = {
            "event": "schedule",
            "type": "schedule",
            "data": {"trips": final_trips},
            "payload": {
                "trips": final_trips
            }
        }
        
        # Hardware/TJ Horner protocol: top-level stopId is usually expected.
        # Use the first stop in our subscription list for compatibility.
        if subs:
            response["payload"]["stopId"] = subs[0]["stopId"]
        elif stop_to_subs:
            first_stop = sorted(stop_to_subs.keys())[0]
            response["payload"]["stopId"] = first_stop

        await ws.send(json.dumps(response))
        self.messages_processed += 1
        self.last_broadcast_time = time.time()
        self.sync_state(last_message=response)

    async def broadcast_loop(self):
        while True:
            # Copy clients to avoid mutation during iteration
            for ws in list(self.clients):
                if ws in self.subscriptions:
                    try:
                        await self.send_update(ws)
                    except websockets.exceptions.ConnectionClosed:
                        # Client disconnected, will be handled by register's finally block
                        pass
                    except Exception as e:
                        print(f"[SERVER] Error updating client {ws.remote_address}: {e}")
            
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
