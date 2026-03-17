# WebSocket Interface Contract

Both the reference container (`ghcr.io/tjhorner/transit-tracker-api:main`) and
our Python proxy implement the same WebSocket protocol. This document captures
the contract and known divergences.

## Subscribe

```json
{
  "event": "schedule:subscribe",
  "data": {
    "routeStopPairs": "<pairs-string>",
    "limit": 10
  }
}
```

### routeStopPairs format

**Reference container:** `<feed>:<routeId>,<feed>:<stopId>` separated by `;`

Example: `puget_sound:40_100240,puget_sound:1_8494;puget_sound:1_100039,puget_sound:1_11920`

**Our proxy:** `<routeId>,<stopId>,<offsetSeconds>` separated by `;`

Example: `40_100240,1_8494,0;1_100039,1_11920,0`

## Response

Both containers push updates with this shape:

```json
{
  "event": "schedule",
  "data": {
    "trips": [
      {
        "tripId": "string",
        "routeId": "string",
        "routeName": "string",
        "headsign": "string",
        "arrivalTime": 1234567890,
        "departureTime": 1234567890,
        "isRealtime": true,
        "stopId": "string",
        "routeColor": "string"
      }
    ]
  }
}
```

## Known Divergences

### Ferry direction filtering

Our proxy filters ferry trips by `arrivalEnabled`/`departureEnabled` OBA flags.
Origin docks show departures only; destination docks show arrivals only. The
reference container does not apply this filtering — it returns all trips for
the route at that stop.

### Ferry vessel name headsigns

Our proxy replaces ferry headsigns with vessel names (e.g., "Sealth") when
`vehicleId` is present in OBA realtime data. The reference container shows
the standard route headsign (e.g., "Bainbridge Island").

### Effective mode time selection

Our proxy uses `effective_mode` logic to choose between `arrivalTime` and
`departureTime` based on stop type (origin vs destination). The reference
container always returns both times as-is from OBA.

### Route name abbreviations

Our proxy applies configurable abbreviation rules to route names. The reference
container returns OBA route names unmodified.
