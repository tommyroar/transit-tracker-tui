#!/usr/bin/env bash
# =============================================================================
# Post-deploy validation: confirm transit_tracker is writing to InfluxDB.
# Runs three Flux queries through the influxdb container and prints the
# parsed counts. Exits non-zero if any check fails.
#
# Requires: INFLUX_ADMIN_TOKEN env (sourced from /Volumes/dev/influxdb/.env
# if not already set).
# =============================================================================
set -euo pipefail

INFLUX_ENV=/Volumes/dev/influxdb/.env
if [ -z "${INFLUX_ADMIN_TOKEN:-}" ]; then
    if [ -r "$INFLUX_ENV" ]; then
        set -a; source "$INFLUX_ENV"; set +a
    fi
fi

if [ -z "${INFLUX_ADMIN_TOKEN:-}" ]; then
    echo "Error: INFLUX_ADMIN_TOKEN not set and $INFLUX_ENV unreadable." >&2
    exit 2
fi

ORG="${INFLUX_ORG:-home}"
BUCKET="${TRANSIT_BUCKET:-transit_tracker}"

run_count() {
    local measurement=$1
    local since=$2
    local flux
    flux=$(cat <<EOF
from(bucket:"$BUCKET")
  |> range(start: $since)
  |> filter(fn: (r) => r._measurement == "$measurement")
  |> count()
EOF
)
    # Sum the _value column across all output series. Annotated CSV from
    # `influx query --raw` looks like:
    #   #group,...
    #   #datatype,...
    #   #default,_result,,,,,,...
    #   ,result,table,_start,_stop,_value,_field,_measurement,...
    #   ,,0,2026-...,2026-...,7,arrival_time_s,trip_prediction,...
    # Data rows start with `,,<table>,...` — empty result + empty table marker.
    docker exec influxdb influx query --org "$ORG" --token "$INFLUX_ADMIN_TOKEN" --raw "$flux" 2>/dev/null \
        | awk -F',' '
            /^#/ { next }
            /^,result,table/ { for (i=1;i<=NF;i++) if ($i=="_value") vi=i; next }
            NF > 1 && vi { sum += $vi }
            END { print sum+0 }
        '
}

trip_count=$(run_count trip_prediction -5m)
counter_count=$(run_count service_counter -5m)
gauge_count=$(run_count service_gauge -5m)

echo "Last 5 minutes:"
echo "  trip_prediction rows : $trip_count"
echo "  service_counter rows : $counter_count"
echo "  service_gauge   rows : $gauge_count"

failed=0
if [ "$trip_count" -lt 1 ]; then
    echo "FAIL: expected ≥1 trip_prediction row" >&2
    failed=1
fi
if [ "$counter_count" -lt 1 ]; then
    echo "FAIL: expected ≥1 service_counter row" >&2
    failed=1
fi
if [ "$gauge_count" -lt 1 ]; then
    echo "FAIL: expected ≥1 service_gauge row" >&2
    failed=1
fi

if [ "$failed" -eq 0 ]; then
    echo "PASS: transit_tracker is writing to InfluxDB."
fi
exit "$failed"
