#!/usr/bin/env bash
# =============================================================================
# Start the Transit Tracker Docker container.
#
# Usage:
#   scripts/start_container.sh                        # foreground (--rm)
#   scripts/start_container.sh --detach               # background (detached)
#   scripts/start_container.sh --config path/to.yaml  # custom config
#   scripts/start_container.sh --detach --config path/to.yaml
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE_NAME="transit-tracker"
CONTAINER_NAME="transit-tracker"
WS_PORT=8000
HTTP_PORT=8080
CONFIG_PATH="$PROJECT_DIR/.local/home.yaml"
DETACH=false

# ---- Parse arguments ----
while [[ $# -gt 0 ]]; do
    case "$1" in
        --detach)
            DETACH=true
            shift
            ;;
        --config)
            CONFIG_PATH="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--detach] [--config <path>]"
            exit 1
            ;;
    esac
done

# Resolve config to absolute path
CONFIG_PATH="$(cd "$(dirname "$CONFIG_PATH")" && pwd)/$(basename "$CONFIG_PATH")"

if [ ! -f "$CONFIG_PATH" ]; then
    echo "Error: Config file not found: $CONFIG_PATH"
    exit 1
fi

cd "$PROJECT_DIR"

# ---- Build image if not present ----
if ! docker image inspect "$IMAGE_NAME" >/dev/null 2>&1; then
    echo "Image '$IMAGE_NAME' not found — building..."
    docker build -t "$IMAGE_NAME" .
else
    echo "Image '$IMAGE_NAME' found."
fi

# ---- Run container ----
if [ "$DETACH" = true ]; then
    echo "Starting container '$CONTAINER_NAME' (detached, restart=always)..."
    docker run -d \
        --name "$CONTAINER_NAME" \
        --restart=always \
        -p "$WS_PORT:$WS_PORT" \
        -p "$HTTP_PORT:$HTTP_PORT" \
        -v "$CONFIG_PATH:/config/config.yaml:ro" \
        "$IMAGE_NAME"

    # Wait for WebSocket port to accept connections (up to 60s)
    echo "Waiting for WebSocket port $WS_PORT to accept connections..."
    TIMEOUT=60
    ELAPSED=0
    while [ "$ELAPSED" -lt "$TIMEOUT" ]; do
        if docker exec "$CONTAINER_NAME" sh -c "echo > /dev/tcp/localhost/$WS_PORT" 2>/dev/null || \
           nc -z localhost "$WS_PORT" 2>/dev/null; then
            echo "Container '$CONTAINER_NAME' is ready (WebSocket on :$WS_PORT)."
            exit 0
        fi
        sleep 1
        ELAPSED=$((ELAPSED + 1))
    done

    echo "Warning: WebSocket port $WS_PORT did not respond within ${TIMEOUT}s."
    echo "Container is running — check logs with: docker logs $CONTAINER_NAME"
    exit 1
else
    echo "Starting container '$CONTAINER_NAME' (foreground)..."
    docker run --rm \
        --name "$CONTAINER_NAME" \
        -p "$WS_PORT:$WS_PORT" \
        -p "$HTTP_PORT:$HTTP_PORT" \
        -v "$CONFIG_PATH:/config/config.yaml:ro" \
        "$IMAGE_NAME"
fi
