import asyncio
import json
import os
import random
import time
from collections import defaultdict
from typing import Any, Dict

import websockets

from ..config import TransitConfig, get_last_config_path
from ..display import format_trip_line
from ..gtfs_schedule import GTFSSchedule
from ..logging import get_logger, is_message_logging_enabled
from ..metrics import metrics
from ..transit_api import TransitAPI

log = get_logger("transit_tracker.server")

SERVICE_STATE_FILE = os.path.join(os.path.expanduser("~/.config/transit-tracker"), "service_state.json")

# Official WSDOT Ferry Vessel Mapping (Agency 95)
WSF_VESSELS = {
    "1": "Cathlamet", "2": "Chelan", "3": "Issaquah", "4": "Kitsap",
    "5": "Kittitas", "6": "Muckleshoot", "7": "Puyallup", "8": "Samish",
    "9": "Sealth", "10": "Suquamish", "11": "Tacoma", "12": "Tillikum",
    "13": "Tokitae", "14": "Walla Walla", "15": "Wenatchee", "16": "Yakima",
    "17": "Kaleetan", "18": "Kitsap", "19": "Kittitas", "20": "Cathlamet", # Some IDs overlap or vary by feed
    "25": "Puyallup", "28": "Sealth", "30": "Spokane", "32": "Tacoma",
    "33": "Tillikum", "36": "Walla Walla", "37": "Wenatchee", "38": "Yakima",
    "52": "Kennewick", "65": "Chetzemoka", "66": "Salish", "68": "Tokitae",
    "69": "Samish", "74": "Chimacum", "75": "Suquamish"
}

def get_service_state() -> Dict[str, Any]:
    if os.path.exists(SERVICE_STATE_FILE):
        try:
            with open(SERVICE_STATE_FILE, "r") as f:
                state = json.load(f)
                return state
        except Exception:
            pass
    return {}

def get_last_service_update() -> str:
    state = get_service_state()
    ts = state.get("last_update")
    if ts:
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))
    return "Never"

class TransitServer:
    def __init__(self, config: TransitConfig):
        self.config = config
        self.config_path = get_last_config_path()
        self.api = TransitAPI(oba_api_key=config.service.oba_api_key)
        self.clients = set()
        self.subscriptions = {} # ws -> List[Dict] (pairs)
        self.client_names = {} # ws -> str
        self.client_limits = {} # ws -> int

        # Centralized cache for arrivals
        # stop_id -> (timestamp, List[arrivals])
        self.cache = {}
        self.rate_limited_stops = set() # Stops currently hitting 429
        self.rate_limit_until = {} # stop_id -> timestamp when retry is allowed

        # Exponential Backoff State
        self.base_interval = self.config.service.check_interval_seconds
        self.current_refresh_interval = self.base_interval
        self.max_refresh_interval = 600 # 10 minutes max backoff

        self.messages_processed = 0
        self.start_time = time.time()
        self.last_broadcast_time = 0

        # Throttle metrics
        self.throttle_total = 0          # lifetime 429 count
        self.throttle_session_start = time.time()
        self.api_calls_total = 0         # lifetime OBA API calls
        self.throttle_log_file = os.path.join(os.path.dirname(SERVICE_STATE_FILE), "throttle_log.jsonl")

        # GTFS static schedule fallback (None if DB not built yet)
        self.gtfs = GTFSSchedule()

        # Sync initial gauge values
        metrics.refresh_interval.set(self.current_refresh_interval)

    def _record_metrics_snapshot(self):
        """Push current gauge values into the time-series ring buffers."""
        now = time.time()
        metrics.active_clients.set(len(self.clients))
        metrics.active_clients_ts.record(len(self.clients), now)
        metrics.refresh_interval.set(self.current_refresh_interval)
        metrics.refresh_interval_ts.record(self.current_refresh_interval, now)
        metrics.cache_size.set(len(self.cache))
        rate = self.throttle_total / max(1, self.api_calls_total)
        metrics.throttle_rate_ts.record(rate * 100, now)

    def sync_state(self, last_message=None):
        """Updates the shared state file for the GUI/TUI to consume."""
        try:
            client_details = []
            for ws in self.clients:
                addr = getattr(ws, "remote_address", ("unknown", 0))
                name = self.client_names.get(ws, "Unknown")
                subs = len(self.subscriptions.get(ws, []))
                client_details.append({
                    "address": f"{addr[0]}:{addr[1]}",
                    "name": name,
                    "subscriptions": subs
                })

            state = {
                "last_update": time.time(),
                "heartbeat": time.time(),
                "start_time": self.start_time,
                "messages_processed": self.messages_processed,
                "pid": os.getpid(),
                "status": "active",
                "clients": client_details,
                "client_count": len(self.clients),
                "is_rate_limited": len(self.rate_limited_stops) > 0,
                "refresh_interval": int(self.current_refresh_interval),
                "throttle_total": self.throttle_total,
                "api_calls_total": self.api_calls_total,
                "throttle_rate": round(self.throttle_total / max(1, self.api_calls_total), 3),
                "uptime_hours": round((time.time() - self.start_time) / 3600, 2),
            }
            if last_message is not None:
                self._last_message = last_message
            if hasattr(self, "_last_message"):
                state["last_message"] = self._last_message

            if self.config_path:
                state["config_path"] = self.config_path

            os.makedirs(os.path.dirname(SERVICE_STATE_FILE), exist_ok=True)
            with open(SERVICE_STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception:
            log.debug("Failed to write service state file", exc_info=True)

        self._record_metrics_snapshot()

    def _log_throttle(self, stop_id: str):
        """Append a throttle event to the persistent JSONL log."""
        try:
            entry = {
                "ts": time.time(),
                "stop": stop_id,
                "interval": int(self.current_refresh_interval),
                "total_429s": self.throttle_total,
                "total_calls": self.api_calls_total,
            }
            os.makedirs(os.path.dirname(self.throttle_log_file), exist_ok=True)
            with open(self.throttle_log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            log.debug("Failed to write throttle log", exc_info=True)

    async def get_arrivals_cached(self, clean_stop_id: str):
        """Returns arrivals for a stop, fetching from OBA only if not recently cached."""
        now = time.time()

        # Skip rate-limited stops until their cooldown expires
        if clean_stop_id in self.rate_limited_stops:
            retry_at = self.rate_limit_until.get(clean_stop_id, 0)
            if now < retry_at:
                return self.cache.get(clean_stop_id, (0, []))[1]
            # Cooldown expired — allow retry
            self.rate_limited_stops.discard(clean_stop_id)

        if clean_stop_id in self.cache:
            ts, data = self.cache[clean_stop_id]
            # Use cached data if it's within the current refresh interval (which may
            # be longer than base_interval during backoff) minus a 2s buffer for safety
            if now - ts < (self.current_refresh_interval - 2):
                return data

        # Fetch fresh
        log.info("OBA API call for %s", clean_stop_id, extra={"component": "server", "stop_id": clean_stop_id})
        self.api_calls_total += 1
        metrics.api_calls.inc()
        t0 = time.time()
        try:
            arrivals = await self.api.get_arrivals(clean_stop_id)
            latency_ms = (time.time() - t0) * 1000
            metrics.api_latency.record(latency_ms)
            self.cache[clean_stop_id] = (now, arrivals)
            self.rate_limited_stops.discard(clean_stop_id)
            self.rate_limit_until.pop(clean_stop_id, None)
            return arrivals
        except Exception as e:
            latency_ms = (time.time() - t0) * 1000
            metrics.api_latency.record(latency_ms)
            metrics.api_errors.inc()
            if "429" in str(e):
                self.throttle_total += 1
                metrics.throttle_events.inc()
                self._log_throttle(clean_stop_id)
                log.warning("Rate limited (429) for %s", clean_stop_id, extra={"component": "server", "stop_id": clean_stop_id})
                self.rate_limited_stops.add(clean_stop_id)
                self.rate_limit_until[clean_stop_id] = now + self.current_refresh_interval
                raise e
            else:
                log.error("OBA error for %s: %s", clean_stop_id, e, extra={"component": "server", "stop_id": clean_stop_id})
            return self.cache.get(clean_stop_id, (0, []))[1] # Fallback to stale cache if any

    def normalize_id(self, item_id):
        if item_id is None: return ""
        s_id = str(item_id)
        if s_id.startswith("wsf:"):
            return s_id.replace("wsf:", "95_")

        if ":" in s_id and "_" in s_id:
            c_idx = s_id.find(":")
            u_idx = s_id.find("_")
            if c_idx < u_idx:
                return s_id[c_idx+1:]
        return s_id

    def apply_abbreviations(self, name: str) -> str:
        """Applies route name abbreviation rules and fixes arrow characters."""
        if not name:
            return name

        # Replace arrow symbols with ">" for better display compatibility
        name = name.replace("->", ">").replace("\u2192", ">")

        for abbr in self.config.transit_tracker.abbreviations:
            if abbr.original.lower() == name.lower():
                return abbr.short
        return name

    async def register(self, ws):
        self.clients.add(ws)
        addr = ws.remote_address
        metrics.ws_connections.inc()
        log.info("Client connected: %s", addr, extra={"component": "server", "client": f"{addr[0]}:{addr[1]}"})
        self.sync_state()
        try:
            async for message in ws:
                metrics.messages_received.inc()
                if is_message_logging_enabled():
                    log.debug("WS RECV from %s: %s", addr, message, extra={"component": "server", "direction": "recv"})
                payload = json.loads(message)
                event = payload.get("event")

                if event == "schedule:subscribe":
                    data = payload.get("data", {})
                    pairs_str = data.get("routeStopPairs", "")
                    self.client_names[ws] = payload.get("client_name") or data.get("client_name") or "Hardware Controller"

                    pairs = []
                    if pairs_str:
                        # Format: routeId,stopId[,offset];...
                        for entry in pairs_str.split(";"):
                            parts = entry.split(",")
                            if len(parts) >= 2:
                                r_id, s_id = parts[0], parts[1]
                                offset = int(parts[2]) if len(parts) > 2 else 0
                                pairs.append({"routeId": r_id, "stopId": s_id, "offset": offset})
                    elif pairs_str == "":
                        log.info("Client sent empty routeStopPairs, using server defaults", extra={"component": "server"})
                        for sub in self.config.subscriptions:
                            off_sec = 0
                            try:
                                import re
                                match = re.search(r"(-?\d+)", str(sub.time_offset))
                                if match: off_sec = int(match.group(1)) * 60
                            except: pass
                            pairs.append({"routeId": sub.route, "stopId": sub.stop, "offset": off_sec})

                    if pairs:
                        self.subscriptions[ws] = pairs
                        limit = payload.get("limit")
                        if limit:
                            self.client_limits[ws] = int(limit)

                        self.sync_state()
                        log.info("Client %s subscribed to %d pairs", ws.remote_address, len(pairs),
                                 extra={"component": "server", "pairs": len(pairs)})
                        # Send immediate update from cache (or fetch if new)
                        await self.send_update(ws)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if ws in self.clients: self.clients.remove(ws)
            self.subscriptions.pop(ws, None)
            self.client_names.pop(ws, None)
            self.client_limits.pop(ws, None)
            metrics.ws_disconnections.inc()
            self.sync_state()
            log.info("Client disconnected: %s", addr, extra={"component": "server", "client": f"{addr[0]}:{addr[1]}"})

    async def refresh_all_data(self):
        """Refreshes OBA data for every unique stop currently in use by any client."""
        unique_stops = set()
        for subs in self.subscriptions.values():
            for s in subs:
                unique_stops.add(self.normalize_id(s["stopId"]))

        if not unique_stops:
            return

        log.info("Refreshing data for %d unique stops", len(unique_stops), extra={"component": "server"})
        metrics.api_calls_ts.record(len(unique_stops))

        any_429 = False
        spacing_sec = self.config.service.request_spacing_ms / 1000.0
        stops_list = sorted(unique_stops)
        for i, clean_id in enumerate(stops_list):
            if i > 0 and spacing_sec > 0:
                jittered = spacing_sec * random.uniform(0.75, 1.25)
                await asyncio.sleep(jittered)
            try:
                await self.get_arrivals_cached(clean_id)
            except Exception as e:
                if "429" in str(e):
                    any_429 = True
                    break  # Stop firing requests once rate-limited

        # Exponential Backoff Logic
        if any_429:
            # Double the interval on any rate limit
            self.current_refresh_interval = min(self.max_refresh_interval, self.current_refresh_interval * 2)
            log.warning("Backing off — next refresh in %ds", int(self.current_refresh_interval),
                        extra={"component": "server", "interval": int(self.current_refresh_interval)})
        else:
            # Gradually decrease interval on success (recovery)
            if self.current_refresh_interval > self.base_interval:
                # Reduce by 20% or back to base
                new_interval = max(self.base_interval, self.current_refresh_interval * 0.8)
                if new_interval != self.current_refresh_interval:
                    self.current_refresh_interval = new_interval
                    log.info("Recovery — refresh interval reduced to %ds", int(self.current_refresh_interval),
                             extra={"component": "server", "interval": int(self.current_refresh_interval)})

    async def send_update(self, ws: websockets.WebSocketServerProtocol):
        subs = self.subscriptions.get(ws, [])
        if not subs: return

        all_trips = []
        stop_to_subs = defaultdict(list)
        for s in subs:
            stop_to_subs[s["stopId"]].append(s)

        for stop_id, stop_subs in stop_to_subs.items():
            try:
                clean_stop_id = self.normalize_id(stop_id)
                # Pull from cache (immediate hit if refresh_all_data just ran)
                # Note: We use a non-raising version for broadcast so it doesn't fail the loop
                if clean_stop_id in self.cache:
                    arrivals = self.cache[clean_stop_id][1]
                elif self.gtfs is not None and self.gtfs.is_available():
                    # Cache miss — use GTFS static schedule as immediate fallback
                    route_ids = {self.normalize_id(s.get("routeId", "")) for s in stop_subs}
                    arrivals = self.gtfs.get_next_departures(
                        stop_id=clean_stop_id,
                        route_ids=route_ids,
                        now=time.time(),
                        count=10,
                    )
                    # GTFS returns bare (un-prefixed) route IDs; restore the agency prefix
                    # from the stop_id so the route is_match check below works correctly.
                    agency_pfx = clean_stop_id.split("_", 1)[0] if "_" in clean_stop_id and clean_stop_id.split("_", 1)[0].isdigit() else ""
                    if agency_pfx:
                        for arr in arrivals:
                            r = arr.get("routeId", "")
                            if r and "_" not in r:
                                arr["routeId"] = f"{agency_pfx}_{r}"
                else:
                    # Cache miss — data_refresh_loop will populate shortly; skip for now
                    arrivals = []

                route_to_sub = {self.normalize_id(s.get("routeId")): s for s in stop_subs}
                relevant_routes = set(route_to_sub.keys())

                now_ts = int(time.time())
                display_mode = self.config.transit_tracker.time_display

                for arr in arrivals:
                    full_route_id = arr.get("routeId", "")
                    normalized_route_id = self.normalize_id(full_route_id)

                    is_match = not relevant_routes or "" in relevant_routes or normalized_route_id in relevant_routes
                    if is_match:
                        # Use predicted if available, fallback to scheduled
                        pred_arr = arr.get("predictedArrivalTime")
                        sched_arr = arr.get("scheduledArrivalTime")
                        pred_dep = arr.get("predictedDepartureTime")
                        sched_dep = arr.get("scheduledDepartureTime")

                        raw_arr = pred_arr if (pred_arr and pred_arr > 0) else sched_arr
                        raw_dep = pred_dep if (pred_dep and pred_dep > 0) else sched_dep

                        if not raw_arr and not raw_dep:
                            # Try the single 'arrivalTime' field from our own TransitAPI results
                            raw_arr = arr.get("arrivalTime")
                            raw_dep = arr.get("departureTime") or raw_arr

                        if not raw_arr: continue

                        if raw_arr > 10**12: raw_arr //= 1000
                        if raw_dep and raw_dep > 10**12: raw_dep //= 1000

                        sub = route_to_sub.get(normalized_route_id) or stop_subs[0]
                        offset_sec = sub.get("offset", 0)

                        # robustness: if the preferred time is missing or in the distant past
                        # (OBA sometimes has stale values for one but not the other), fall back.
                        now_minus_buffer = now_ts - 60 # 1 minute ago

                        # OBA provides per-trip flags indicating whether this stop is an
                        # arrival or departure point. Use them to pick the right time:
                        #   departureEnabled=True  → ferry leaving this dock (use departure)
                        #   arrivalEnabled=True    → ferry approaching this dock (use arrival)
                        # Falls back to the global display_mode for non-ferry / missing flags.
                        dep_enabled = arr.get("departureEnabled")
                        arr_enabled = arr.get("arrivalEnabled")
                        if dep_enabled is True and not arr_enabled:
                            effective_mode = "departure"
                        elif arr_enabled is True and not dep_enabled:
                            effective_mode = "arrival"
                        else:
                            effective_mode = display_mode

                        is_ferry = full_route_id.startswith("95_") or "wsf" in full_route_id.lower()

                        # For ferries, skip trips whose OBA flags explicitly indicate the
                        # wrong direction. e.g. at Seattle Terminal (display_mode="departure"),
                        # a BI→SEA arrival (arrivalEnabled=True, departureEnabled=False) is skipped.
                        if is_ferry:
                            if display_mode == "departure" and arr_enabled is True and not dep_enabled:
                                continue
                            if display_mode == "arrival" and dep_enabled is True and not arr_enabled:
                                continue

                        if effective_mode == "departure":
                            base_time = raw_dep if (raw_dep and raw_dep > now_minus_buffer) else (None if is_ferry else raw_arr)
                        else:
                            base_time = raw_arr if (raw_arr and raw_arr > now_minus_buffer) else (None if is_ferry else raw_dep)

                        if not base_time or base_time < now_minus_buffer:
                            continue

                        final_display_time = base_time + offset_sec

                        # For ferries, replace headsign with vessel name when vehicleId is live;
                        # fall back to destination headsign (e.g. "Bainbridge Island") otherwise,
                        # since different vessels serve different scheduled runs and caching the
                        # last-seen vessel would show the wrong name for upcoming trips.
                        headsign = self.apply_abbreviations(str(arr.get("headsign") or arr.get("tripHeadsign") or "Transit"))
                        route_name = self.apply_abbreviations(str(arr.get("routeName") or arr.get("routeShortName") or ""))
                        if is_ferry:
                            vehicle_id_full = arr.get("vehicleId") or (arr.get("tripStatus") or {}).get("vehicleId")
                            if vehicle_id_full:
                                vehicle_id_short = vehicle_id_full.split("_")[-1]
                                vessel_name = WSF_VESSELS.get(vehicle_id_short)
                                if vessel_name:
                                    headsign = vessel_name

                        all_trips.append({
                            "tripId": str(arr.get("tripId", "")),
                            "routeId": str(full_route_id),
                            "routeName": route_name,
                            "routeColor": str(arr.get("routeColor", "")) if arr.get("routeColor") else None,
                            "stopId": str(stop_id),
                            "headsign": headsign,
                            "arrivalTime": int(final_display_time),
                            "departureTime": int((raw_dep or raw_arr) + offset_sec),
                            "isRealtime": bool(arr.get("vehicleId")) if is_ferry else bool(arr.get("isRealtime"))
                        })
            except Exception as e:
                log.error("Exception in send_update: %s", e, exc_info=True, extra={"component": "server"})
                # 429s during send_update are ignored (we use last cache)
                pass

        all_trips.sort(key=lambda x: x.get("arrivalTime", 0))
        limit = self.client_limits.get(ws, 3)
        final_trips = all_trips[:limit]

        # TJ Horner protocol uses 'data' key, not 'payload'
        response = {
            "event": "schedule",
            "data": {
                "trips": final_trips
            }
        }

        try:
            msg_json = json.dumps(response)
            await ws.send(msg_json)
            self.messages_processed += 1
            metrics.messages_sent.inc()
            metrics.messages_rate_ts.record(1)

            if is_message_logging_enabled():
                addr = getattr(ws, "remote_address", (None,))
                log.debug("WS SEND to %s: %s", addr, msg_json, extra={"component": "server", "direction": "send"})
            else:
                addr = getattr(ws, "remote_address", (None,))
                if addr[0] and addr[0] != "127.0.0.1":
                    fmt = self.config.transit_tracker.display_format if self.config else None
                    lines = [format_trip_line(t, time.time(), fmt=fmt) for t in final_trips]
                    log.info("Push to %s: %s", addr[0], " | ".join(lines) or "0 trips", extra={"component": "server"})
            self.sync_state(last_message=response)
        except Exception:
            pass

    async def data_refresh_loop(self):
        """Background task that keeps the shared cache fresh with exponential backoff."""
        while True:
            try:
                # Hot-reload check
                current_path = get_last_config_path()
                if current_path and current_path != self.config_path:
                    log.info("Config path changed: %s — reloading", current_path, extra={"component": "server"})
                    self.config = TransitConfig.load(current_path)
                    self.config_path = current_path
                    self.cache.clear() # Clear cache on config change to avoid stale data for new stops

                await self.refresh_all_data()
            except Exception as e:
                log.error("Refresh loop exception: %s", e, exc_info=True, extra={"component": "server"})

            # Wait for the current (possibly backed-off) interval
            await asyncio.sleep(self.current_refresh_interval)

    async def broadcast_loop(self):
        """Background task that pushes the latest cached data to all clients."""
        last_heartbeat = 0
        while True:
            now = time.time()
            send_heartbeat = (now - last_heartbeat >= 10)

            for ws in list(self.clients):
                if send_heartbeat:
                    try: await ws.send(json.dumps({"event": "heartbeat", "data": None}))
                    except: pass

                if ws in self.subscriptions:
                    try: await self.send_update(ws)
                    except websockets.exceptions.ConnectionClosed: pass
                    except Exception as e:
                        log.error("Broadcast error: %s", e, extra={"component": "server"})

            if send_heartbeat: last_heartbeat = now
            self.sync_state()
            # Broadcast loop remains consistent to keep hardware alive
            await asyncio.sleep(self.base_interval)

async def run_server(host: str = "0.0.0.0", port: int = 8000, config: TransitConfig = None):
    if config is None: config = TransitConfig.load()
    server = TransitServer(config)
    log.info("Starting Transit Tracker API on %s:%d", host, port, extra={"component": "server"})

    async with websockets.serve(server.register, host, port):
        # Run both the refresh and broadcast loops concurrently
        await asyncio.gather(
            server.data_refresh_loop(),
            server.broadcast_loop()
        )
