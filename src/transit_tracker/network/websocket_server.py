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
                        addr = getattr(ws, "remote_address", None)
                        if addr and (addr[0].startswith("192.168.") or addr[0].startswith("10.0.")):
                             self.client_names[ws] = "Hardware Controller"
                    
                    pairs = []
                    # Case 1: routeStopPairs string (TJ Horner style)
                    # Support: routeId,stopId[,offset]
                    pairs_str = payload.get("routeStopPairs")
                    if pairs_str:
                        for pair in pairs_str.split(";"):
                            parts = [p.strip() for p in pair.split(",")]
                            if len(parts) >= 2:
                                r_id, s_id = parts[0], parts[1]
                                offset = int(parts[2]) if len(parts) >= 3 else 0
                                pairs.append({"routeId": r_id, "stopId": s_id, "offset": offset})
                    elif pairs_str == "":
                        # FALLBACK: Use server-side config if board sends empty string
                        print(f"[SERVER] Client sent empty routeStopPairs, using server defaults.")
                        for sub in self.config.subscriptions:
                            # Map the human-readable 'time_offset' to seconds
                            off_sec = 0
                            try:
                                import re
                                match = re.search(r"(-?\d+)", str(sub.time_offset))
                                if match: off_sec = int(match.group(1)) * 60
                            except: pass
                            pairs.append({"routeId": sub.route, "stopId": sub.stop, "offset": off_sec})

                    if pairs:
                        self.subscriptions[ws] = pairs
                        # Store limit if provided
                        limit = payload.get("limit")
                        if limit:
                            self.client_limits[ws] = int(limit)
                        
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
                
                now_ts = int(time.time())
                found_at_stop = 0

                for arr in arrivals:
                    # Use full IDs for the response to match cloud proxy
                    full_route_id = arr.get("routeId", "")
                    normalized_route_id = normalize_id(full_route_id)
                    
                    # Match if relevant_routes is empty (all routes) or if normalized IDs match
                    is_match = not relevant_routes or "" in relevant_routes or None in relevant_routes or normalized_route_id in relevant_routes
                    
                    if is_match:
                        # 1. Get raw timestamps
                        arr_val = arr.get("predictedArrivalTime") or arr.get("scheduledArrivalTime") or arr.get("arrivalTime")
                        dep_val = arr.get("predictedDepartureTime") or arr.get("scheduledDepartureTime") or arr.get("departureTime") or arr_val
                        
                        if not arr_val: continue
                        if isinstance(arr_val, str) and arr_val.isdigit(): arr_val = int(arr_val)
                        if isinstance(dep_val, str) and dep_val.isdigit(): dep_val = int(dep_val)
                        
                        # 2. Convert to seconds
                        if arr_val > 10**12: arr_val //= 1000
                        if dep_val > 10**12: dep_val //= 1000
                        
                        # 3. Apply the per-pair offset (from the subscription handshake)
                        sub = route_to_sub.get(normalized_route_id) or stop_subs[0]
                        offset_sec = sub.get("offset", 0)
                        
                        final_arrival = arr_val + offset_sec
                        final_departure = dep_val + offset_sec

                        # 4. STRICT FILTERING (Original Project Behavior)
                        if final_arrival < now_ts - 60:
                            continue

                        route_name = arr.get("routeName") or arr.get("routeShortName") or ""
                        headsign = arr.get("headsign") or arr.get("tripHeadsign") or "Transit"
                        is_realtime = bool(arr.get("isRealtime") or "predictedArrivalTime" in arr)

                        all_trips.append({
                            "tripId": str(arr.get("tripId", "")),
                            "routeId": str(full_route_id),
                            "routeName": str(route_name),
                            "routeColor": str(arr.get("routeColor", "")) if arr.get("routeColor") else None,
                            "stopId": str(stop_id),
                            "headsign": str(headsign),
                            "arrivalTime": int(final_arrival),
                            "departureTime": int(final_departure),
                            "isRealtime": is_realtime
                        })
                        found_at_stop += 1
                
                if found_at_stop > 0:
                    print(f"[SERVER] Found {found_at_stop} active trips for stop {stop_id}", flush=True)
            except Exception as e:
                print(f"[SERVER] Error processing stop {stop_id}: {e}", flush=True)

        # 5. SORTING & LAPPING (Original Project Behavior)
        all_trips.sort(key=lambda x: x.get("arrivalTime", 0))
        limit = self.client_limits.get(ws, 3)
        final_trips = all_trips[:limit]
        
        # 6. BUILD RESPONSE (Match TJ Horner protocol exactly)
        response = {
            "event": "schedule",
            "data": {"trips": final_trips}
        }

        try:
            msg_json = json.dumps(response)
            addr = getattr(ws, "remote_address", None)
            if addr and addr[0] != "127.0.0.1":
                print(f"[SERVER] Sending {len(final_trips)} trips to {addr}: {msg_json[:150]}...", flush=True)
                
            await ws.send(msg_json)
            self.messages_processed += 1
            self.last_broadcast_time = time.time()
            self.sync_state(last_message=response)
        except Exception as e:
            print(f"[SERVER] Error sending update to {getattr(ws, 'remote_address', 'Unknown')}: {e}", flush=True)

    async def broadcast_loop(self):
        last_heartbeat = 0
        while True:
            now = time.time()
            
            # Send heartbeat every 10 seconds to prevent hardware timeout
            send_heartbeat = (now - last_heartbeat >= 10)
            
            # Copy clients to avoid mutation during iteration
            for ws in list(self.clients):
                # 1. Handle Heartbeat
                if send_heartbeat:
                    try:
                        await ws.send(json.dumps({"event": "heartbeat", "data": None}))
                    except:
                        pass
                
                # 2. Handle Schedule Updates
                if ws in self.subscriptions:
                    try:
                        await self.send_update(ws)
                    except websockets.exceptions.ConnectionClosed:
                        # Client disconnected, will be handled by register's finally block
                        pass
                    except Exception as e:
                        print(f"[SERVER] Error updating client {ws.remote_address}: {e}")
            
            if send_heartbeat:
                last_heartbeat = now

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
