import asyncio
import json

import websockets

from ..config import TransitConfig, build_route_stop_pairs
from ..logging import get_logger, is_message_logging_enabled
from ..metrics import metrics

log = get_logger("transit_tracker.client")


async def run_service(config: TransitConfig = None):
    """
    Background service that maintains a connection to the transit API.
    Used for monitoring and potentially other background tasks.
    In 1-to-1 mode, this acts as a verification client for the local proxy.
    """
    from ..config import get_last_config_path

    if config is None:
        config = TransitConfig.load()

    current_path = get_last_config_path()
    api_url = config.api_url

    log.info("Starting background monitor, connecting to %s", api_url, extra={"component": "client"})

    while True:
        try:
            # Check for config reload
            new_path = get_last_config_path()
            if new_path and new_path != current_path:
                log.info("Config path changed: %s — reloading", new_path, extra={"component": "client"})
                config = TransitConfig.load(new_path)
                current_path = new_path
                api_url = config.api_url

            async with websockets.connect(api_url) as ws:
                log.info("Connected to %s", api_url, extra={"component": "client"})

                # Build TJ Horner style routeStopPairs string for all subscriptions
                pairs_str = build_route_stop_pairs(config.subscriptions)

                if pairs_str:
                    sub_msg = json.dumps({
                        "event": "schedule:subscribe",
                        "client_name": "BackgroundMonitor",
                        "data": {
                            "routeStopPairs": pairs_str
                        }
                    })
                    await ws.send(sub_msg)
                    if is_message_logging_enabled():
                        log.debug("WS SEND: %s", sub_msg, extra={"component": "client", "direction": "send"})

                async for message in ws:
                    metrics.messages_received.inc()
                    if is_message_logging_enabled():
                        log.debug("WS RECV: %s", message, extra={"component": "client", "direction": "recv"})

                    # Check for config change while connected
                    check_path = get_last_config_path()
                    if check_path and check_path != current_path:
                        log.info("Config changed while connected — reconnecting", extra={"component": "client"})
                        break

                    data = json.loads(message)
                    if data.get("event") == "schedule":
                        # Use 'data' key to match TJ Horner protocol
                        d = data.get("data") or {}
                        trips = d.get("trips", [])
                        if trips:
                            first = trips[0]
                            route = first.get("routeName", "??")
                            log.info("Received update: %d trips. Next: %s in %s (Unix)",
                                     len(trips), route, first.get("arrivalTime"),
                                     extra={"component": "client"})
        except Exception as e:
            log.warning("Connection error: %s — retrying in 10s", e, extra={"component": "client"})
            await asyncio.sleep(10)
