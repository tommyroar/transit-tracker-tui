"""Long-lived WebSocket cache feeding the Home Assistant tile endpoints.

A single ``TileCache`` task runs alongside ``run_web``. It connects to the
internal WS server (``ws://localhost:8000``) once, subscribes to every
configured route/stop pair, and stores the most recent ``schedule`` payload
keyed by normalised stop_id.

The HA tile endpoints (``/api/tiles`` and ``/api/tile/<stop_id>``) read
straight out of this cache and render through ``tile.build_stop_tile``,
so each REST poll is a dict lookup + a couple of list comprehensions —
no upstream round-trip, no risk of duplicating OBA API load.
"""

import asyncio
import json
import time
from typing import Optional

import websockets

from ..config import TransitConfig
from ..logging import get_logger
from ..tile import _normalize_id, build_stop_tile

log = get_logger("transit_tracker.web")


class TileCache:
    """Background WS client + in-memory per-stop cache of raw trips."""

    def __init__(
        self,
        config: TransitConfig,
        ws_url: str = "ws://localhost:8000",
        tile_limit: int = 5,
    ):
        self.config = config
        self.ws_url = ws_url
        self.tile_limit = tile_limit
        self.running = True
        # normalised stop_id -> {"trips": [raw_trip, ...], "updated_ms": int}
        self._cache: dict[str, dict] = {}

    # -- Subscription payload --

    def build_subscribe_payload(self) -> dict:
        """Build the ``schedule:subscribe`` message for every configured pair.

        Duplicates the format used by ``BaseSimulator.build_subscribe_payload``
        — same ``routeStopPairs`` string — but lives here to avoid pulling
        the simulator module (and all its Rich-related transitive deps)
        into the web server's startup path.
        """
        import re

        pairs = []
        for sub in self.config.subscriptions:
            r_id = sub.route if ":" in sub.route else f"{sub.feed}:{sub.route}"
            s_id = sub.stop if ":" in sub.stop else f"{sub.feed}:{sub.stop}"
            off_sec = 0
            match = re.search(r"(-?\d+)", str(sub.time_offset))
            if match:
                off_sec = int(match.group(1)) * 60
            pairs.append(f"{r_id},{s_id},{off_sec}")

        return {
            "event": "schedule:subscribe",
            "client_name": "TileCache",
            "data": {
                "routeStopPairs": ";".join(pairs),
                "limit": 20,
            },
        }

    # -- Read-side: tile builders --

    def get_tile(self, stop_id: str) -> Optional[dict]:
        """Return the tile for a given stop_id, or None if not configured."""
        stop_cfg = None
        for stop in self.config.transit_tracker.stops:
            if stop.stop_id == stop_id:
                stop_cfg = stop
                break
        if stop_cfg is None:
            return None

        target = _normalize_id(stop_id)
        entry = self._cache.get(target, {"trips": [], "updated_ms": 0})
        subs = [s for s in self.config.subscriptions if s.stop == stop_id]
        return build_stop_tile(
            stop_cfg,
            subs,
            entry["trips"],
            current_time_ms=int(time.time() * 1000),
            time_display=self.config.transit_tracker.time_display,
            limit=self.tile_limit,
        )

    def list_tiles(self) -> list[dict]:
        """Return one tile per configured stop, preserving config order."""
        now_ms = int(time.time() * 1000)
        td = self.config.transit_tracker.time_display
        out: list[dict] = []
        for stop in self.config.transit_tracker.stops:
            target = _normalize_id(stop.stop_id)
            entry = self._cache.get(target, {"trips": [], "updated_ms": 0})
            subs = [s for s in self.config.subscriptions if s.stop == stop.stop_id]
            out.append(
                build_stop_tile(
                    stop,
                    subs,
                    entry["trips"],
                    current_time_ms=now_ms,
                    time_display=td,
                    limit=self.tile_limit,
                )
            )
        return out

    # -- Write-side: WS listener --

    def _ingest_trips(self, trips: list[dict]) -> None:
        """Partition a broadcast payload by stop_id and overwrite the cache.

        Each ``schedule`` message is a full snapshot for the subscribed stops,
        so we overwrite per stop rather than accumulate.
        """
        now_ms = int(time.time() * 1000)
        by_stop: dict[str, list[dict]] = {}
        for trip in trips:
            stop = _normalize_id(trip.get("stopId", ""))
            if not stop:
                continue
            by_stop.setdefault(stop, []).append(trip)

        for stop, trip_list in by_stop.items():
            self._cache[stop] = {"trips": trip_list, "updated_ms": now_ms}

    async def run(self) -> None:
        """Connect, subscribe, listen, reconnect-with-backoff. Forever."""
        if not self.config.subscriptions:
            log.info(
                "TileCache: no subscriptions configured — skipping",
                extra={"component": "web"},
            )
            return

        payload_json = json.dumps(self.build_subscribe_payload())
        backoff = 1.0

        while self.running:
            try:
                async with websockets.connect(self.ws_url) as ws:
                    await ws.send(payload_json)
                    log.info(
                        "TileCache connected to %s",
                        self.ws_url,
                        extra={"component": "web"},
                    )
                    backoff = 1.0
                    async for raw in ws:
                        if not self.running:
                            break
                        try:
                            msg = json.loads(raw)
                        except (TypeError, json.JSONDecodeError):
                            continue
                        if msg.get("event") != "schedule":
                            continue
                        trips = msg.get("data", {}).get("trips", [])
                        self._ingest_trips(trips)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if not self.running:
                    return
                log.debug(
                    "TileCache reconnect in %.1fs: %s",
                    backoff,
                    e,
                    extra={"component": "web"},
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
