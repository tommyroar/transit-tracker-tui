#!/bin/sh
# =============================================================================
# Transit Tracker container entrypoint
#
# Starts both the WebSocket service (:8000) and HTTP web server (:8080)
# as background processes, then waits for either to exit.
# =============================================================================
set -e

CONFIG_PATH="/config/config.yaml"
SETTINGS_DIR="$HOME/.config/transit-tracker"
SETTINGS_FILE="$SETTINGS_DIR/settings.yaml"

# ---- Config discovery ----
# If a config file is mounted at /config/config.yaml, register it so the CLI
# picks it up via get_last_config_path().  Otherwise, defaults apply.
if [ -f "$CONFIG_PATH" ]; then
    echo "[ENTRYPOINT] Found mounted config at $CONFIG_PATH"
    mkdir -p "$SETTINGS_DIR"
    cat > "$SETTINGS_FILE" <<EOF
last_config_path: $CONFIG_PATH
EOF
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
