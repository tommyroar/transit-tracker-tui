#!/bin/sh
# =============================================================================
# Transit Tracker container entrypoint
#
# Starts both the WebSocket service (:8000) and HTTP web server (:8080)
# as background processes, then waits for either to exit.
# =============================================================================
set -e

export SERVICE_SETTINGS_PATH="${SERVICE_SETTINGS_PATH:-/config/service.yaml}"
SERVICE_YAML="$SERVICE_SETTINGS_PATH"
PROFILES_DIR="${PROFILES_DIR:-/config/profiles}"
export PROFILES_DIR

# ---- Config discovery ----
# If service.yaml already has a last_config_path that exists, use it.
# Otherwise, pick the first available profile from the profiles directory.
_resolve_default_profile() {
    # Check if service.yaml already points to a valid profile
    if [ -f "$SERVICE_YAML" ]; then
        EXISTING=$(grep "^last_config_path:" "$SERVICE_YAML" 2>/dev/null | sed 's/^last_config_path: *//')
        if [ -n "$EXISTING" ] && [ -f "$EXISTING" ]; then
            echo "[ENTRYPOINT] Active profile: $EXISTING"
            return
        fi
    fi

    # Find a default profile in the profiles directory
    if [ -d "$PROFILES_DIR" ]; then
        # Prefer home.yaml, then first .yaml file
        if [ -f "$PROFILES_DIR/home.yaml" ]; then
            DEFAULT_PROFILE="$PROFILES_DIR/home.yaml"
        else
            DEFAULT_PROFILE=$(find "$PROFILES_DIR" -maxdepth 1 -name "*.yaml" ! -name "service.yaml" ! -name "service_state.json" | sort | head -1)
        fi

        if [ -n "$DEFAULT_PROFILE" ]; then
            echo "[ENTRYPOINT] Setting default profile: $DEFAULT_PROFILE"
            if [ -f "$SERVICE_YAML" ]; then
                TMP=$(mktemp)
                echo "last_config_path: $DEFAULT_PROFILE" > "$TMP"
                grep -v "^last_config_path:" "$SERVICE_YAML" >> "$TMP"
                cat "$TMP" > "$SERVICE_YAML"
                rm "$TMP"
            else
                cat > "$SERVICE_YAML" 2>/dev/null <<EOF
last_config_path: $DEFAULT_PROFILE
EOF
            fi
            return
        fi
    fi

    # Legacy: single config file at /config/config.yaml
    if [ -f "/config/config.yaml" ]; then
        echo "[ENTRYPOINT] Found legacy mounted config at /config/config.yaml"
        if [ -f "$SERVICE_YAML" ] && ! grep -q "last_config_path" "$SERVICE_YAML" 2>/dev/null; then
            TMP=$(mktemp)
            echo "last_config_path: /config/config.yaml" > "$TMP"
            cat "$SERVICE_YAML" >> "$TMP"
            cat "$TMP" > "$SERVICE_YAML"
            rm "$TMP"
        fi
        return
    fi

    echo "[ENTRYPOINT] No profiles found — using defaults (wss://tt.horner.tj/)"
}

_resolve_default_profile

# ---- Start service ----
# The WebSocket server (:8000), HTTP web server (:8080), and verification
# monitor now run together in a single asyncio event loop, so one process
# serves both ports. exec hands the container's main PID to Python so Docker's
# stop signals reach it directly for a graceful shutdown.
echo "[ENTRYPOINT] Starting Transit Tracker (WebSocket :8000 + HTTP :8080) ..."
exec python -m transit_tracker.cli service
