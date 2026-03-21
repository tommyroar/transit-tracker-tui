#!/bin/sh
# =============================================================================
# Transit Tracker container entrypoint
#
# Starts both the WebSocket service (:8000) and HTTP web server (:8080)
# as background processes, then waits for either to exit.
# =============================================================================
set -e

CONFIG_PATH="/config/config.yaml"
SERVICE_YAML="${SERVICE_SETTINGS_PATH:-/config/service.yaml}"

# ---- Config discovery ----
# If a config file is mounted at /config/config.yaml, register it so the CLI
# picks it up via get_last_config_path().
if [ -f "$CONFIG_PATH" ]; then
    echo "[ENTRYPOINT] Found mounted config at $CONFIG_PATH"
    # Inject last_config_path into service settings (create if missing)
    if [ -f "$SERVICE_YAML" ]; then
        if ! grep -q "last_config_path" "$SERVICE_YAML" 2>/dev/null; then
            # Prepend — sed -i can't write to mounted volumes, so use tmp+cat
            TMP=$(mktemp)
            echo "last_config_path: $CONFIG_PATH" > "$TMP"
            cat "$SERVICE_YAML" >> "$TMP"
            cat "$TMP" > "$SERVICE_YAML"
            rm "$TMP"
        fi
    else
        cat > "$SERVICE_YAML" <<EOF
last_config_path: $CONFIG_PATH
EOF
    fi
else
    echo "[ENTRYPOINT] No config mounted — using defaults (wss://tt.horner.tj/)"
fi

# ---- Start services ----
echo "[ENTRYPOINT] Starting WebSocket service on :8000 ..."
python -m transit_tracker.cli service &
SERVICE_PID=$!

echo "[ENTRYPOINT] Starting HTTP web server on :8080 ..."
python -m transit_tracker.cli web &
WEB_PID=$!

echo "[ENTRYPOINT] Services running (service=$SERVICE_PID, web=$WEB_PID)"

# ---- Wait for either process to exit ----
# If one dies, kill the other and exit with the failed process's code.
wait_for_exit() {
    while true; do
        # Check if either process has exited
        if ! kill -0 "$SERVICE_PID" 2>/dev/null; then
            wait "$SERVICE_PID"
            EXIT_CODE=$?
            echo "[ENTRYPOINT] WebSocket service exited ($EXIT_CODE) — shutting down"
            kill "$WEB_PID" 2>/dev/null || true
            exit "$EXIT_CODE"
        fi
        if ! kill -0 "$WEB_PID" 2>/dev/null; then
            wait "$WEB_PID"
            EXIT_CODE=$?
            echo "[ENTRYPOINT] Web server exited ($EXIT_CODE) — shutting down"
            kill "$SERVICE_PID" 2>/dev/null || true
            exit "$EXIT_CODE"
        fi
        sleep 1
    done
}

# Handle SIGTERM/SIGINT gracefully
trap 'echo "[ENTRYPOINT] Caught signal — stopping services"; kill "$SERVICE_PID" "$WEB_PID" 2>/dev/null; wait; exit 0' TERM INT

wait_for_exit
