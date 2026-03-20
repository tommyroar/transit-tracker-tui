---
inclusion: auto
---

# Reference Container — ghcr.io/tjhorner/transit-tracker-api

## What It Is

The reference container is the upstream project by TJ Horner that this transit tracker is designed to be compatible with. It serves as the baseline for API equivalence testing. The ESP32 hardware firmware was originally built to talk to this container.

- Image: `ghcr.io/tjhorner/transit-tracker-api`
- Source: https://github.com/tjhorner/transit-tracker-api
- Single port: **3000** (serves both WebSocket and HTTP on the same port)
- Cloud instance: `wss://tt.horner.tj/` (public, no auth)

## Protocol

The reference container speaks a JSON-over-WebSocket protocol:

### Client → Server

```json
{
  "event": "schedule:subscribe",
  "data": {
    "routeStopPairs": "40_100240,1_8494,-420;1_100039,1_11920,-540",
    "limit": 5
  }
}
```

`routeStopPairs` is semicolon-separated entries of `routeId,stopId[,offsetSeconds]`. If empty, the server uses its own default config.

### Server → Client

**Schedule push** (every refresh cycle):
```json
{
  "event": "schedule",
  "data": {
    "trips": [
      {
        "tripId": "40_141953498",
        "routeId": "40_100240",
        "routeName": "554",
        "stopId": "1_8494",
        "stopName": "S Bellevue Station Bay 1",
        "headsign": "Downtown Seattle",
        "arrivalTime": 1773534120,
        "departureTime": 1773534180,
        "isRealtime": true
      }
    ]
  }
}
```

**Heartbeat** (every ~10s):
```json
{"event": "heartbeat", "data": null}
```

### Required Trip Fields

Every trip object in a `schedule` response must have exactly these 9 fields:
`tripId`, `routeId`, `routeName`, `stopId`, `stopName`, `headsign`, `arrivalTime`, `departureTime`, `isRealtime`

## Key Differences from This Project

| Aspect | Reference Container | This Project |
|--------|-------------------|--------------|
| Port | 3000 (single) | 8000 (WS) + 8080 (HTTP) |
| ID format | Bare OBA IDs (`1_8494`) | Prefixed IDs (`st:1_8494`, `wsf:7`) |
| Ferries | Not supported | Agency 95, vessel names, abbreviations |
| API spec endpoint | None known | `/spec` (HTML), `/api/spec` (JSON) |
| HTTP endpoints | Minimal | Walkshed, simulator, station map, GeoJSON |

## Equivalence Testing

The equivalence test (`scripts/verify_cloud_equivalence.py --containers`) compares both containers side by side:

- Reference container runs on host port 13000 (mapped from container 3000)
- Local container runs on host port 18000 (mapped from container 8000)
- Both receive the same `schedule:subscribe` with `HANDSHAKE_PAIRS = "40_100240,1_8494,-420;1_100039,1_11920,-540"`
- Comparison checks: top-level keys, per-trip field names, trip counts
- 120-second timeout, skip on timeout

Use `.local/home.yaml` for equivalence testing — it contains only standard Sound Transit stops (no ferries, no `st:` prefixes in the pairs string) so it's valid for both containers.

## Important Notes

- The reference container does NOT have an `/openapi` endpoint. The spec mentions this in requirements/design but it doesn't exist on the reference. This project's equivalent is `/api/spec` (JSON) and `/spec` (HTML docs).
- The reference uses bare OBA IDs in `routeStopPairs` (e.g., `40_100240`), not prefixed IDs. The equivalence handshake strips prefixes accordingly.
- The reference container fetches from the same OneBusAway API, so trip data should be structurally identical for shared stops.
