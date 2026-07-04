# Updates

A curated, reverse-chronological log of notable changes to Transit Tracker. Renders on the
dev-wiki at `/dev-wiki/transit-tracker-tui/docs/updates`. (Auto PR/commit history also appears
on the dev-wiki changelog page.)

## 2026-06-28 — Single-process container (single in-process asyncio loop)

The container now runs one asyncio process instead of two glued by a shell supervisor
([#69](https://github.com/robogeosociety/transit-tracker-tui/pull/69)).

- `docker/entrypoint.sh` resolves the active profile, then `exec`s a single process
  (`python -m transit_tracker.cli service`) that serves the WebSocket proxy (`:8000`), the
  monitor client, and the web server (`:8080`) in one asyncio event loop. Python is PID 1, so
  `docker stop`'s SIGTERM reaches it directly for a clean shutdown.
- The web server and the TileCache client now run in the same process as the proxy
  (`network/websocket_server.py`, `web/server.py`, `web/tile_cache.py`), so both ports are
  served from the one loop with no inner `wait_for_exit` shell babysitting.
- `--restart=always` is the sole supervisor.
